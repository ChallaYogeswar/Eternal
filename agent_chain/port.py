"""Issues one capability per run and the per-tool ROTATE permissions (amendment A3)."""
from __future__ import annotations

from typing import Any, Callable, Dict, Optional

from orchestration.capability import CapabilityIssuer, CapabilityVerifier
from orchestration.consent import ConsentStore

TIER_OF_SENSITIVITY = {"read_only": 0, "write": 1, "sensitive": 2}


def tier_lookup_from_registry(registry: Dict[str, Any]) -> Callable[[str], Optional[int]]:
    """Real tier of a tool, straight from the RCAP manifest registry (not from anything an agent supplied)."""
    def lookup(tool: str) -> Optional[int]:
        entry = registry.get(tool)
        if entry is None:
            return None
        s = getattr(entry.sensitivity, "value", entry.sensitivity)
        return TIER_OF_SENSITIVITY.get(s)
    return lookup


class _Handle:
    def __init__(self, token: str, claims: Dict[str, Any], rotate: Dict[str, str], on_close: Callable[[], None]):
        self.token, self._claims, self._rotate, self._on_close = token, claims, rotate, on_close
        self._closed = False

    def rotate_token(self, tool: str) -> Optional[str]:
        return self._rotate.get(tool)

    def summary(self) -> Dict[str, Any]:
        return dict(self._claims)

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._on_close()


class ChainCapabilityPort:
    """`grant_permission(session_id, tool) -> token` is the chain's own grant (Phase 5's guarded version, which refuses
    non-ACTIVE sessions). If it raises, `begin` raises and the run is refused before any model or tool call: a
    quarantined or killed session cannot start new agent work."""

    def __init__(self, issuer: CapabilityIssuer, verifier: CapabilityVerifier,
                 grant_permission: Callable[[str, str], str], *, consent: Optional[ConsentStore] = None,
                 default_ttl_s: float = 900.0):
        self.issuer, self.verifier, self.grant, self.consent, self.default_ttl_s = issuer, verifier, grant_permission, consent, default_ttl_s

    def begin(self, *, spec: Any, task: Any, session_id: str, origin_channel: str) -> _Handle:
        ttl = min(self.default_ttl_s, float(spec.budget.wall_clock_s) + 60.0)
        token = self.issuer.issue(agent=spec.name, run_id=task.run_id, session_id=session_id,
                                  spec_tools=spec.allowed_tools, spec_max_tier=int(spec.max_tool_tier),
                                  call_cap=int(spec.budget.max_tool_calls), ttl_s=ttl, origin_channel=origin_channel)
        rotate = {t: self.grant(session_id, t) for t in sorted(spec.allowed_tools)}
        claims = {"agent": spec.name, "run_id": task.run_id, "tools": sorted(spec.allowed_tools),
                  "call_cap": int(spec.budget.max_tool_calls), "ttl_s": ttl, "origin": origin_channel}
        run_id = task.run_id

        def on_close() -> None:
            self.verifier.revoke_run(run_id)            # authority ends with the run
            if self.consent is not None:
                self.consent.revoke_run(run_id)         # and so do its consent grants
        return _Handle(token, claims, rotate, on_close)
