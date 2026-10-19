"""
FastAPI wrapper around the Orchestrator — the network-facing side of "one
lightweight daemon on the Raspberry Pi" (build plan Section 3). In
production this should listen ONLY on the Tailscale interface; nothing here
does its own transport-level authentication beyond what QAP-P itself
provides, so it must never be bound to a public interface.

Run directly for local testing:
    uvicorn qap_p.api:app --host 127.0.0.1 --port 8420
"""
import base64
import threading
import time
from typing import Optional

import re

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from api_auth.fastapi_deps import require

from .orchestrator import Orchestrator
from .models import DeviceRole
from .hardware_key import HardwareKeyProvider
from .auth import AuthorizationError
from .vault import VaultError
from .state_machine import StateMachineError
from .quarantine import QuarantineRecoveryError

app = FastAPI(title="QAP-P Orchestrator", version="0.1.0-phase1")
orchestrator = Orchestrator()


def _background_sweeper():
    """QUIESCE-TTL expiry sweep. On the real Pi deployment a systemd timer
    calling POST-equivalent logic on a schedule is just as valid; this
    thread exists so the Phase-1 daemon is self-contained for local testing."""
    while True:
        time.sleep(2)
        try:
            orchestrator.sweep_expired_quiesce()
        except Exception:
            pass


_sweeper_thread = threading.Thread(target=_background_sweeper, daemon=True)
_sweeper_thread.start()


# ---------- request models ----------

class EnrollRequest(BaseModel):
    device_id: str
    role: str
    public_key_pem: Optional[str] = None  # omit to generate a simulated dev key server-side


class ChallengeRequest(BaseModel):
    target_session_id: str


class AuthorizeRequest(BaseModel):
    issuing_device_id: str
    target_session_id: str
    nonce: str
    signature_b64: str


class ReviveRequest(BaseModel):
    authorization_token: str


class QuiesceRequest(BaseModel):
    cpu_load: float = 0.0
    memory_pressure: float = 0.0
    note: str = ""


class QuarantineRequest(BaseModel):
    reason: str


class SoftRecoveryRequest(BaseModel):
    is_enrolled_device: bool
    impossible_travel: bool
    biometric_verified: bool


class HardRecoveryRequest(BaseModel):
    presented_key: str


class TokenRequest(BaseModel):
    device_id: str
    scope: str
    ttl_seconds: Optional[float] = None


class TokenVerifyRequest(BaseModel):
    token: str


class CanaryCheckRequest(BaseModel):
    presented_value: str
    accessor_session_id: str


# ---------- endpoints ----------

@app.post("/devices/enroll", dependencies=[Depends(require("qap:admin"))])
def enroll_device(req: EnrollRequest):
    try:
        role = DeviceRole(req.role)
    except ValueError:
        raise HTTPException(400, f"unknown role '{req.role}'")
    if orchestrator.get_device(req.device_id) is not None:
        raise HTTPException(409, "device already enrolled")

    if req.public_key_pem:
        pem = req.public_key_pem

        class _PreEnrolled(HardwareKeyProvider):
            def sign(self, challenge: bytes) -> bytes:
                raise NotImplementedError("signing happens on the client device, not here")

            def public_key_pem(self) -> str:
                return pem

            @property
            def attestation_type(self) -> str:
                return "client-provided (server cannot verify hardware backing remotely)"

        device = orchestrator.enroll_device(req.device_id, role, _PreEnrolled())
    else:
        device = orchestrator.enroll_device(req.device_id, role)

    return {
        "device_id": device.device_id,
        "public_key_pem": device.public_key_pem,
        "attestation_type": device.attestation_type,
    }


@app.get("/devices/{device_id}/trust-score", dependencies=[Depends(require("qap:read"))])
def get_trust_score(device_id: str):
    try:
        score = orchestrator.compute_trust_score(device_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"device_id": device_id, "trust_score": score}


@app.post("/sessions", dependencies=[Depends(require("qap:write"))])
def create_session(device_id: str):
    try:
        session_id = orchestrator.create_session(device_id)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"session_id": session_id, "state": "ACTIVE"}


@app.get("/sessions/{session_id}", dependencies=[Depends(require("qap:read"))])
def get_session(session_id: str):
    try:
        state = orchestrator.get_session_state(session_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"session_id": session_id, "state": state.value}


@app.post("/sessions/{session_id}/quiesce", dependencies=[Depends(require("qap:write"))])
def quiesce(session_id: str, req: QuiesceRequest):
    try:
        state = orchestrator.quiesce_session(session_id, req.dict())
    except StateMachineError as e:
        raise HTTPException(409, str(e))
    return {"session_id": session_id, "state": state.value}


@app.post("/sessions/{session_id}/quarantine", dependencies=[Depends(require("qap:write"))])
def quarantine(session_id: str, req: QuarantineRequest):
    try:
        state = orchestrator.quarantine_session(session_id, req.reason)
    except StateMachineError as e:
        raise HTTPException(404, str(e))
    return {"session_id": session_id, "state": state.value}


@app.post("/authorize/challenge", dependencies=[Depends(require("qap:write"))])
def authorize_challenge(req: ChallengeRequest):
    try:
        nonce = orchestrator.request_authorization_challenge(req.target_session_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"nonce": nonce}


@app.post("/authorize", dependencies=[Depends(require("qap:write"))])
def authorize(req: AuthorizeRequest):
    try:
        signature = base64.b64decode(req.signature_b64)
    except Exception:
        raise HTTPException(400, "signature_b64 is not valid base64")
    try:
        token = orchestrator.peer_authorize(
            req.issuing_device_id, req.target_session_id, req.nonce, signature
        )
    except AuthorizationError as e:
        raise HTTPException(403, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"authorization_token": token}


@app.post("/sessions/{session_id}/revive", dependencies=[Depends(require("qap:admin"))])
def revive(session_id: str, req: ReviveRequest):
    try:
        state = orchestrator.revive_session(session_id, req.authorization_token)
    except AuthorizationError as e:
        raise HTTPException(403, str(e))
    return {"session_id": session_id, "state": state.value}


@app.post("/sessions/{session_id}/recovery/soft", dependencies=[Depends(require("qap:admin"))])
def recovery_soft(session_id: str, req: SoftRecoveryRequest):
    try:
        ok = orchestrator.recover_soft(
            session_id, req.is_enrolled_device, req.impossible_travel, req.biometric_verified
        )
    except QuarantineRecoveryError as e:
        raise HTTPException(403, str(e))
    if not ok:
        raise HTTPException(403, "soft recovery denied — see ledger for reason")
    return {"session_id": session_id, "state": "ACTIVE"}


@app.post("/sessions/{session_id}/recovery/hard", dependencies=[Depends(require("qap:admin"))])
def recovery_hard(session_id: str, req: HardRecoveryRequest):
    ok = orchestrator.recover_hard(session_id, req.presented_key)
    if not ok:
        raise HTTPException(403, "hard recovery denied — incorrect offline recovery key")
    return {"session_id": session_id, "state": "ACTIVE"}


_SCOPE_RE = re.compile(r"^[a-z0-9_:.\-]{1,64}$")


@app.post("/vault/tokens", dependencies=[Depends(require("qap:admin"))])
def issue_token(req: TokenRequest):
    # Audit E-01: validate scope, cap the lifetime, and only issue for devices that exist.
    if not _SCOPE_RE.match(req.scope):
        raise HTTPException(400, "scope must match [a-z0-9_:.-]{1,64}")
    cap = orchestrator.config.max_token_ttl_seconds
    if req.ttl_seconds is not None and not (0 < req.ttl_seconds <= cap):
        raise HTTPException(400, f"ttl_seconds must be in (0, {cap}]")
    if orchestrator.get_device(req.device_id) is None:
        raise HTTPException(404, "unknown device")
    token = orchestrator.issue_token(req.device_id, req.scope, req.ttl_seconds)
    return {"token": token}


@app.post("/vault/tokens/verify", dependencies=[Depends(require("qap:read"))])
def verify_token(req: TokenVerifyRequest):
    try:
        payload = orchestrator.verify_token(req.token)
    except VaultError as e:
        raise HTTPException(403, str(e))
    return payload


@app.post("/canary/plant", dependencies=[Depends(require("qap:admin"))])
def plant_canary():
    value = orchestrator.plant_canary()
    return {"canary_value": value}


@app.post("/canary/check", dependencies=[Depends(require("qap:write"))])
def check_canary(req: CanaryCheckRequest):
    triggered = orchestrator.check_canary(req.presented_value, req.accessor_session_id)
    return {"triggered": triggered}


@app.post("/chaos/run", dependencies=[Depends(require("qap:admin"))])
def run_chaos(target_session_id: str):
    results = orchestrator.run_chaos_suite(target_session_id)
    return {"results": results}


@app.get("/ledger/verify", dependencies=[Depends(require("ledger:read"))])
def ledger_verify():
    ok, bad_id = orchestrator.verify_ledger()
    return {"intact": ok, "first_broken_entry_id": bad_id}


@app.get("/ledger", dependencies=[Depends(require("ledger:read"))])
def ledger_recent(limit: int = 100):
    return {"entries": orchestrator.ledger_entries(limit)}


@app.get("/healthz")
def healthz():
    from api_auth.fastapi_deps import get_registry
    return {"status": "ok", "auth_configured": get_registry().configured}
