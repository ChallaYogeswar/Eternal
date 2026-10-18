"""
Laptop agent data/action layer — Eternal_Master_Build_Plan.md Section 7.1:
"Laptop agent: small FastAPI service exposing a *fixed, named* action
list only — lock/unlock, sleep/wake, open app X, run script Y, shut down.
Never a generic 'run this command' endpoint."

Self-contained by necessity, not just by precedent: in a real deployment,
this MCP server runs ON the physical laptop being controlled — a
genuinely different machine from the one running Eternal's core/gateway
(Pi or mini-PC). The `device_control_gateway` package is never installed on that laptop;
there is no cross-package import available to reach for even if it were
convenient. (Compare mcp_servers/aether/data.py, which is self-contained
by precedent/consistency since ÆTHER colocates with Eternal's own host —
here it's a hard requirement, not a style choice, and the two reasons are
worth telling apart.) `server.py`'s tool functions call the module-level
store below and don't know or care whether it's backed by real OS calls
or the simulated state machine — same seam-swapping discipline as every
earlier phase.

"wake" is deliberately NOT a method here. A sleeping (or shut-down)
laptop cannot run code to wake itself — nothing is running on it. Waking
it is a Wake-on-LAN packet sent from a DIFFERENT, always-on machine (the
Pi, presumed to share the laptop's home LAN segment, since WoL broadcast
does not cross routers) — see mcp_servers/remote_control/data.py, which
is a separate server for exactly this reason: it must still be reachable
and functional while this laptop_agent server is unreachable (asleep).

Real vs. simulated, stated plainly: this sandbox has no active GUI/login
session (headless container) — "lock the screen" has nothing to lock —
and actually executing shutdown/suspend here would kill the sandbox this
code is running in. That is a different, more conservative reason to
simulate than "the model weights aren't reachable" (Phase 4's STT/wake-
word/speaker-verify seams) or "the package registry is blocked" (Phase
2's Ollama/HF seams) — stated here explicitly rather than blurred
together with those.
"""
import json
import os
import platform
from enum import Enum
from pathlib import Path
from typing import Optional

ALLOWED_APPS = ("browser", "code_editor", "terminal", "mail")
ALLOWED_SCRIPTS = ("backup_notes", "sync_aether", "restart_ollama")
DEFAULT_STATE_PATH = str(Path(__file__).resolve().parent / "_sim_state.json")


class LaptopActionError(ValueError):
    """Caller-facing failure (unknown app/script name, action invalid for
    the current state) — a ValueError subclass so RCAP-P's gateway (which
    already turns a tool-side ValueError into a clean, non-crashing
    ToolCallResult) handles it the same way it handles every other data
    layer's own error type (AetherStoreError, etc.), no special-casing
    needed upstream."""


class PlatformActionUnavailableError(RuntimeError):
    """Raised by the real per-OS providers — see module docstring for why
    this environment specifically cannot honestly perform these actions."""


class PowerState(str, Enum):
    AWAKE = "awake"
    ASLEEP = "asleep"
    SHUTDOWN = "shutdown"


class LockState(str, Enum):
    LOCKED = "locked"
    UNLOCKED = "unlocked"


class LaptopActionProvider:
    """Base class documenting the six fixed, named, agent-side actions —
    build plan Section 7.1's list minus 'wake' (see module docstring).
    Subclasses never accept arbitrary text to execute; open_app/run_script
    validate against ALLOWED_APPS/ALLOWED_SCRIPTS before doing anything —
    the SAME validation the MCP tool layer in server.py also performs
    (defense in depth: two independent checks, not one checked twice)."""

    def lock(self) -> "LaptopActionOutcome": raise NotImplementedError
    def unlock(self) -> "LaptopActionOutcome": raise NotImplementedError
    def sleep(self) -> "LaptopActionOutcome": raise NotImplementedError
    def open_app(self, app_name: str) -> "LaptopActionOutcome": raise NotImplementedError
    def run_script(self, script_name: str) -> "LaptopActionOutcome": raise NotImplementedError
    def shutdown(self) -> "LaptopActionOutcome": raise NotImplementedError


class LaptopActionOutcome:
    def __init__(self, ok: bool, action: str, detail: str,
                 power_state: Optional[PowerState] = None, lock_state: Optional[LockState] = None):
        self.ok = ok
        self.action = action
        self.detail = detail
        self.power_state = power_state
        self.lock_state = lock_state

    def as_dict(self) -> dict:
        return {
            "ok": self.ok, "action": self.action, "detail": self.detail,
            "power_state": self.power_state.value if self.power_state else None,
            "lock_state": self.lock_state.value if self.lock_state else None,
        }


class SimulatedLaptopActionProvider(LaptopActionProvider):
    """Real, tested double: genuine power/lock state with genuine
    transition rules (you cannot lock a sleeping machine; you cannot open
    an app on a locked one) — the state machine itself is real logic, not
    a stub that always returns ok=True.

    Persists to a small JSON file when constructed with an explicit
    state_path (get_provider()'s singleton does this); an ad-hoc
    SimulatedLaptopActionProvider() with no state_path stays in-memory
    only, exactly the original behavior every unit test relies on.

    Persistence exists because rcap_p.mcp_client.make_stateless_executor
    (checked directly) spawns a FRESH subprocess for every single tool
    call — 'lock' and the 'open_app' that checks whether the screen is
    locked run in two different OS processes, so an in-memory-only
    instance would silently forget every prior action between them.
    mcp_servers/aether never hit this because its store is SQLite-file-
    backed already; this class had the same requirement and simply hadn't
    been given the same treatment until this was caught by running the
    real subprocess end-to-end script rather than only in-process tests.
    A REAL per-OS provider (Windows/macOS/Linux, below) has no equivalent
    problem — it would ask the actual OS for lock/power state on every
    call, which is inherently 'persisted' by the OS itself; this
    file-backed persistence exists only for the simulated double."""

    def __init__(self, state_path: Optional[str] = None):
        # state_path=None (the default) means NO persistence — an
        # isolated, in-memory-only instance, exactly the original
        # behavior every existing test call site (bare
        # SimulatedLaptopActionProvider()) already relies on. Persistence
        # only activates when a caller explicitly opts in by passing a
        # path — see get_provider() below, the one call site that does.
        # Same convention as mcp_servers/home_assistant/data.py's
        # FixtureHomeAssistantClient, for the same reason.
        self._persist = state_path is not None
        self.state_path = state_path
        self.power_state = PowerState.AWAKE
        self.lock_state = LockState.UNLOCKED
        if self._persist:
            self._load()

    def _load(self) -> None:
        try:
            with open(self.state_path, "r") as f:
                raw = json.load(f)
            self.power_state = PowerState(raw["power_state"])
            self.lock_state = LockState(raw["lock_state"])
        except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
            pass  # keep the freshly-seeded defaults already set above

    def _save(self) -> None:
        if not self._persist:
            return
        # write-to-temp-then-replace: a reader (a concurrently-spawned
        # subprocess) never observes a half-written file.
        tmp_path = f"{self.state_path}.tmp"
        with open(tmp_path, "w") as f:
            json.dump({"power_state": self.power_state.value, "lock_state": self.lock_state.value}, f)
        os.replace(tmp_path, self.state_path)

    def lock(self) -> LaptopActionOutcome:
        if self.power_state != PowerState.AWAKE:
            return LaptopActionOutcome(False, "lock", f"cannot lock: machine is {self.power_state.value}",
                                        self.power_state, self.lock_state)
        self.lock_state = LockState.LOCKED
        self._save()
        return LaptopActionOutcome(True, "lock", "screen locked", self.power_state, self.lock_state)

    def unlock(self) -> LaptopActionOutcome:
        if self.power_state != PowerState.AWAKE:
            return LaptopActionOutcome(False, "unlock", f"cannot unlock: machine is {self.power_state.value}",
                                        self.power_state, self.lock_state)
        self.lock_state = LockState.UNLOCKED
        self._save()
        return LaptopActionOutcome(True, "unlock", "screen unlocked", self.power_state, self.lock_state)

    def sleep(self) -> LaptopActionOutcome:
        if self.power_state != PowerState.AWAKE:
            return LaptopActionOutcome(False, "sleep", f"already {self.power_state.value}",
                                        self.power_state, self.lock_state)
        self.power_state = PowerState.ASLEEP
        self._save()
        return LaptopActionOutcome(True, "sleep", "machine suspended", self.power_state, self.lock_state)

    def open_app(self, app_name: str) -> LaptopActionOutcome:
        if app_name not in ALLOWED_APPS:
            raise LaptopActionError(f"'{app_name}' is not in the allowed app list {ALLOWED_APPS}")
        if self.power_state != PowerState.AWAKE:
            return LaptopActionOutcome(False, "open_app", f"cannot open app: machine is {self.power_state.value}")
        if self.lock_state != LockState.UNLOCKED:
            return LaptopActionOutcome(False, "open_app", "cannot open app: screen is locked")
        return LaptopActionOutcome(True, "open_app", f"opened '{app_name}'", self.power_state, self.lock_state)

    def run_script(self, script_name: str) -> LaptopActionOutcome:
        if script_name not in ALLOWED_SCRIPTS:
            raise LaptopActionError(f"'{script_name}' is not in the allowed script list {ALLOWED_SCRIPTS}")
        if self.power_state != PowerState.AWAKE:
            return LaptopActionOutcome(False, "run_script", f"cannot run script: machine is {self.power_state.value}")
        return LaptopActionOutcome(True, "run_script", f"ran '{script_name}'", self.power_state, self.lock_state)

    def shutdown(self) -> LaptopActionOutcome:
        if self.power_state == PowerState.SHUTDOWN:
            return LaptopActionOutcome(False, "shutdown", "already shut down", self.power_state, self.lock_state)
        self.power_state = PowerState.SHUTDOWN
        self._save()
        return LaptopActionOutcome(True, "shutdown", "machine shut down", self.power_state, self.lock_state)


def _unavailable(action: str) -> PlatformActionUnavailableError:
    return PlatformActionUnavailableError(
        f"'{action}' cannot be honestly performed in this environment: no active GUI/login "
        "session exists to act on (headless sandbox), and actually shutting down or "
        "suspending would take down the environment this code is running in. Use "
        "SimulatedLaptopActionProvider for development/testing; deploy this module to the "
        "real target laptop to use it for real."
    )


class WindowsLaptopActionProvider(LaptopActionProvider):
    """Documented seam. Real commands this WOULD run (Windows 10/11):
    lock -> `rundll32.exe user32.dll,LockWorkStation`; sleep ->
    `rundll32.exe powrprof.dll,SetSuspendState 0,1,0`; open_app ->
    subprocess.Popen against a resolved, allow-listed executable path
    (never the caller's raw string); run_script -> a pre-registered
    `.ps1`/`.bat` path, same allow-list discipline; shutdown ->
    `shutdown /s /t 0`. 'unlock' has no safe unattended equivalent on
    Windows without a stored credential the vault would have to hold in a
    form the OS lock screen itself can accept — deployment note, not
    implemented as a raw command here; see README 'Open Decisions'."""

    def lock(self): raise _unavailable("lock")
    def unlock(self): raise _unavailable("unlock")
    def sleep(self): raise _unavailable("sleep")
    def open_app(self, app_name): raise _unavailable("open_app")
    def run_script(self, script_name): raise _unavailable("run_script")
    def shutdown(self): raise _unavailable("shutdown")


class MacOSLaptopActionProvider(LaptopActionProvider):
    """Documented seam. Real commands this WOULD run (macOS): lock ->
    `pmset displaysleepnow`; sleep -> `pmset sleepnow`; open_app ->
    `open -a <AllowlistedAppName>`; run_script -> a pre-registered script
    path via `osascript`/`bash`; shutdown -> `osascript -e 'tell app
    "System Events" to shut down'`. Same 'unlock' note as Windows."""

    def lock(self): raise _unavailable("lock")
    def unlock(self): raise _unavailable("unlock")
    def sleep(self): raise _unavailable("sleep")
    def open_app(self, app_name): raise _unavailable("open_app")
    def run_script(self, script_name): raise _unavailable("run_script")
    def shutdown(self): raise _unavailable("shutdown")


class LinuxLaptopActionProvider(LaptopActionProvider):
    """Documented seam. Real commands this WOULD run (systemd): lock ->
    `loginctl lock-session`; sleep -> `systemctl suspend`; open_app -> a
    pre-registered `.desktop` entry/binary path; run_script -> a
    pre-registered script path; shutdown -> `systemctl poweroff`. Same
    'unlock' note as Windows."""

    def lock(self): raise _unavailable("lock")
    def unlock(self): raise _unavailable("unlock")
    def sleep(self): raise _unavailable("sleep")
    def open_app(self, app_name): raise _unavailable("open_app")
    def run_script(self, script_name): raise _unavailable("run_script")
    def shutdown(self): raise _unavailable("shutdown")


def real_provider_for_this_platform() -> LaptopActionProvider:
    """Dispatches by platform.system() — the same seam-selection principle
    Phase 4 used per-capability (WhisperSTT vs FixtureSTT availability);
    here the switch is per-OS instead of per-model-availability."""
    system = platform.system()
    if system == "Windows":
        return WindowsLaptopActionProvider()
    if system == "Darwin":
        return MacOSLaptopActionProvider()
    if system == "Linux":
        return LinuxLaptopActionProvider()
    raise PlatformActionUnavailableError(f"no LaptopActionProvider implemented for platform {system!r}")


_provider: Optional[LaptopActionProvider] = None


def get_provider() -> LaptopActionProvider:
    """Module-level singleton — same pattern as aether/data.py's
    get_store(). PHASE5_LAPTOP_REAL=1 opts into the real per-OS seam
    (which will raise PlatformActionUnavailableError in this sandbox);
    default is the simulated provider, constructed WITH persistence
    enabled (see the class docstring for why the singleton specifically
    needs it while ad-hoc test instances do not)."""
    global _provider
    if _provider is None:
        if os.environ.get("PHASE5_LAPTOP_REAL") == "1":
            _provider = real_provider_for_this_platform()
        else:
            default_path = os.environ.get("PHASE5_LAPTOP_STATE_PATH", DEFAULT_STATE_PATH)
            _provider = SimulatedLaptopActionProvider(state_path=default_path)
    return _provider


def reset_provider_for_tests() -> None:
    global _provider
    _provider = SimulatedLaptopActionProvider()
