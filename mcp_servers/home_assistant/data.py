"""
Home Assistant data layer — Eternal_Master_Build_Plan.md Section 7.1:
"Home devices: Home Assistant joins the same Tailscale mesh; MCP-scoped
tool access, reachable from anywhere."

Self-contained by the same precedent as mcp_servers/aether/data.py and
mcp_servers/cinemavault/data.py: server.py's tool functions call the
module-level functions below and don't know or care whether they're
backed by a real HA instance or the fixture. Unlike laptop_agent, this
server COULD import from the device_control_gateway package in a real deployment (it runs
co-located with Eternal's core, just making outbound HTTP calls to a
separately-running HA box) — kept self-contained anyway for the same
independent-testability and standalone-launchability reasons every other
MCP server in this project has, not because it's structurally required
here the way it is for laptop_agent.

This talks Home Assistant's real, documented REST API: `POST /api/
services/<domain>/<service>` with `{"entity_id": ...}` to act, `GET
/api/states/<entity_id>` to read, `Authorization: Bearer <token>`. Only
the four service calls Phase 5's tools need (lock, unlock, turn_on,
turn_off) plus one state read — a thin wrapper, not a general HA client.

Real vs. simulated, stated plainly: this sandbox's network egress is
restricted to package registries and source-code hosts (see the
environment's network configuration) — there is no reachable Home
Assistant instance here, by sandbox policy, not by any flaw in this
client. RealHomeAssistantClient is genuine httpx client code that will
work against a real HA base_url + long-lived token; it raises
HomeAssistantUnavailableError with a specific reason (not configured vs.
unreachable vs. HA-side error) rather than fabricating state.
FixtureHomeAssistantClient is the tested double, seeded with a few
simulated entities so the SENSITIVE/WRITE/READ_ONLY gating above this
layer (device_control_gateway/registry_ext.py) has real reads/writes to exercise, not
canned responses.
"""
import os
from pathlib import Path
import json
from typing import Any, Dict, Optional

HOME_ASSISTANT_BASE_URL = os.environ.get("HOME_ASSISTANT_BASE_URL", "")
HOME_ASSISTANT_TOKEN = os.environ.get("HOME_ASSISTANT_TOKEN", "")
HOME_ASSISTANT_TIMEOUT_SECONDS = float(os.environ.get("PHASE5_HA_TIMEOUT", "5.0"))

VALID_DOMAINS = ("lock", "light", "switch", "sensor")


class HomeAssistantUnavailableError(RuntimeError):
    pass


class EntityState:
    def __init__(self, entity_id: str, domain: str, state: str, attributes: Optional[Dict[str, Any]] = None):
        self.entity_id = entity_id
        self.domain = domain
        self.state = state
        self.attributes = attributes or {}

    def as_dict(self) -> dict:
        return {"entity_id": self.entity_id, "domain": self.domain, "state": self.state, "attributes": self.attributes}


class HomeAssistantClient:
    def call_service(self, domain: str, service: str, entity_id: str) -> EntityState: raise NotImplementedError
    def get_state(self, entity_id: str) -> EntityState: raise NotImplementedError


class RealHomeAssistantClient(HomeAssistantClient):
    """Real REST client. Requires httpx and a reachable base_url/token —
    both genuinely optional at construction time so importing this module
    never fails just because a deployment hasn't configured HA yet."""

    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {HOME_ASSISTANT_TOKEN}", "Content-Type": "application/json"}

    def _require_configured(self):
        if not HOME_ASSISTANT_BASE_URL:
            raise HomeAssistantUnavailableError(
                "HOME_ASSISTANT_BASE_URL is not configured. Set it (and HOME_ASSISTANT_TOKEN) to "
                "the real Home Assistant instance's Tailscale address to use this client for real; "
                "use FixtureHomeAssistantClient for development/testing without a live HA instance."
            )

    def call_service(self, domain: str, service: str, entity_id: str) -> EntityState:
        import httpx

        self._require_configured()
        url = f"{HOME_ASSISTANT_BASE_URL}/api/services/{domain}/{service}"
        try:
            resp = httpx.post(url, json={"entity_id": entity_id}, headers=self._headers(),
                               timeout=HOME_ASSISTANT_TIMEOUT_SECONDS)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise HomeAssistantUnavailableError(f"Home Assistant call to {url} failed: {e}") from e
        return self.get_state(entity_id)

    def get_state(self, entity_id: str) -> EntityState:
        import httpx

        self._require_configured()
        url = f"{HOME_ASSISTANT_BASE_URL}/api/states/{entity_id}"
        try:
            resp = httpx.get(url, headers=self._headers(), timeout=HOME_ASSISTANT_TIMEOUT_SECONDS)
            resp.raise_for_status()
            payload = resp.json()
        except httpx.HTTPError as e:
            raise HomeAssistantUnavailableError(f"Home Assistant call to {url} failed: {e}") from e
        domain = entity_id.split(".", 1)[0]
        return EntityState(entity_id, domain, payload.get("state", "unknown"), payload.get("attributes", {}))


class FixtureHomeAssistantClient(HomeAssistantClient):
    """Tested double with real, PERSISTED state transitions — calling
    'lock' on an already-locked entity is idempotent and reflects that;
    calling a service against an unknown entity_id raises, exactly like a
    real HA instance would (HA returns a 4xx for an unrecognized
    entity_id).

    State persists to a small JSON file (PHASE5_HA_STATE_PATH, or a
    default under this file's directory), not just an in-memory dict.
    Found necessary, not merely nice-to-have — same reason as
    mcp_servers/laptop_agent/data.py's SimulatedLaptopActionProvider (see
    its class docstring for the fuller version): rcap_p.mcp_client.
    make_stateless_executor spawns a fresh subprocess per tool call, so
    an in-memory-only fixture would forget an unlock the instant the
    subprocess that performed it exited. RealHomeAssistantClient has no
    equivalent issue — the real HA instance itself is what persists
    state; this file-backed persistence exists only for the fixture."""

    DEFAULT_ENTITIES: Dict[str, Dict[str, Any]] = {
        "lock.front_door": {"domain": "lock", "state": "locked", "attributes": {}},
        "lock.back_door": {"domain": "lock", "state": "unlocked", "attributes": {}},
        "light.living_room": {"domain": "light", "state": "off", "attributes": {}},
        "switch.office_heater": {"domain": "switch", "state": "off", "attributes": {}},
        "sensor.front_door_battery": {"domain": "sensor", "state": "87", "attributes": {"unit": "%"}},
    }

    def __init__(self, entities: Optional[Dict[str, Dict[str, Any]]] = None, state_path: Optional[str] = None):
        # state_path=None (the default) means NO persistence — an
        # isolated, in-memory-only instance, exactly the original
        # behavior every existing test call site (bare
        # FixtureHomeAssistantClient()) already relies on. Persistence
        # only activates when a caller explicitly opts in by passing a
        # path — see get_client() below, the one call site that does.
        self._persist = state_path is not None
        self.state_path = state_path
        seed = entities if entities is not None else self.DEFAULT_ENTITIES
        self.entities: Dict[str, Dict[str, Any]] = {k: dict(v, attributes=dict(v["attributes"])) for k, v in seed.items()}
        if self._persist:
            self._load()

    def _load(self) -> None:
        try:
            with open(self.state_path, "r") as f:
                raw = json.load(f)
            self.entities = raw
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            pass  # keep the freshly-seeded defaults already set in __init__

    def _save(self) -> None:
        if not self._persist:
            return
        tmp_path = f"{self.state_path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump(self.entities, f)
        os.replace(tmp_path, self.state_path)

    def call_service(self, domain: str, service: str, entity_id: str) -> EntityState:
        if entity_id not in self.entities:
            raise HomeAssistantUnavailableError(f"unknown entity_id '{entity_id}'")
        entity = self.entities[entity_id]
        if entity["domain"] != domain:
            raise HomeAssistantUnavailableError(f"'{entity_id}' is domain '{entity['domain']}', not '{domain}'")
        new_state = {"lock": "locked", "unlock": "unlocked", "turn_on": "on", "turn_off": "off"}.get(service)
        if new_state is None:
            raise HomeAssistantUnavailableError(f"unknown service '{service}' for domain '{domain}'")
        entity["state"] = new_state
        self._save()
        return self.get_state(entity_id)

    def get_state(self, entity_id: str) -> EntityState:
        if entity_id not in self.entities:
            raise HomeAssistantUnavailableError(f"unknown entity_id '{entity_id}'")
        e = self.entities[entity_id]
        return EntityState(entity_id, e["domain"], e["state"], dict(e["attributes"]))


_client: Optional[HomeAssistantClient] = None


def get_client() -> HomeAssistantClient:
    """Module-level singleton — same pattern as every other data.py's
    get_store()/get_provider(). PHASE5_HA_REAL=1 opts into the real REST
    client (which raises HomeAssistantUnavailableError unless
    HOME_ASSISTANT_BASE_URL is genuinely configured); default is the
    fixture, constructed WITH persistence enabled (see the class
    docstring for why the singleton specifically needs it while ad-hoc
    test instances do not)."""
    global _client
    if _client is None:
        if os.environ.get("PHASE5_HA_REAL") == "1":
            _client = RealHomeAssistantClient()
        else:
            default_path = os.environ.get("PHASE5_HA_STATE_PATH", str(Path(__file__).resolve().parent / "_sim_state.json"))
            _client = FixtureHomeAssistantClient(state_path=default_path)
    return _client


def reset_client_for_tests() -> None:
    global _client
    _client = FixtureHomeAssistantClient()
