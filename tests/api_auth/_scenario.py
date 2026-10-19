"""Run in a subprocess (module-level daemons read env/cwd at import). Prints a JSON map of observations."""
import importlib
import json
import sys

from fastapi.testclient import TestClient

from api_auth.fastapi_deps import set_registry
from api_auth.principals import AuthRegistry, hash_token, new_token

mod_name, sensitive_tool = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "")
roles = {"owner": "owner_app", "owner2": "owner_app", "voice": "voice_client", "auto": "automation", "ro": "readonly"}
toks = {k: new_token() for k in roles}
mode = sys.argv[3] if len(sys.argv) > 3 else "configured"
set_registry(AuthRegistry([] if mode == "none" else
                          [{"id": k, "role": r, "token_sha256": hash_token(toks[k])} for k, r in roles.items()]))
app = importlib.import_module(mod_name).app
c = TestClient(app, raise_server_exceptions=False)
H = lambda k: {"Authorization": f"Bearer {toks[k]}"}                      # noqa: E731
out = {}


def rec(name, r):
    try:
        body = r.json()
    except Exception:
        body = None
    out[name] = {"status": r.status_code, "body": body}


body = {"tool_name": "web_search", "arguments": {"query": "x"}, "explanation": "t", "session_id": "nope"}
rec("healthz", c.get("/healthz"))
rec("ledger_noauth", c.get("/ledger"))
rec("ledger_ro", c.get("/ledger", headers=H("ro")))
rec("call_noauth", c.post("/tools/call", json=body))
rec("call_badtoken", c.post("/tools/call", headers={"Authorization": "Bearer etn_x"}, json=body))
rec("call_ro", c.post("/tools/call", headers=H("ro"), json=body))
for who in ("voice", "auto", "owner"):
    rec(f"call_{who}", c.post("/tools/call", headers=H(who), json=body))
rec("voice_claims_typed", c.post("/tools/call", headers=H("voice"), json={**body, "confirmation_channel": "text_typed"}))
rec("voice_claims_tap", c.post("/tools/call", headers=H("voice"), json={**body, "confirmation_channel": "app_tap"}))
rec("auto_claims_owner", c.post("/tools/call", headers=H("auto"), json={**body, "caller_kind": "owner_interactive"}))
rec("auto_claims_tap", c.post("/tools/call", headers=H("auto"), json={**body, "confirmation_channel": "app_tap"}))
rec("grant_noauth", c.post("/tools/grant", params={"session_id": "s", "tool_name": "web_search"}))
rec("grant_ro", c.post("/tools/grant", headers=H("ro"), json={"session_id": "s", "tool_name": "web_search"},
                       params={"session_id": "s", "tool_name": "web_search"}))
if mod_name != "rcap_p.api":
    cg = {"params": {"tool_name": "web_search"}, "json": {"tool_name": "web_search"}}
    kw = {"json": cg["json"]} if mod_name == "voice_consent_gateway.api" else {"params": cg["params"]}
    rec("consent_noauth", c.post("/consent/grant", **kw))
    rec("consent_voice", c.post("/consent/grant", headers=H("voice"), **kw))
    rec("consent_auto", c.post("/consent/grant", headers=H("auto"), **kw))
    rec("consent_owner", c.post("/consent/grant", headers=H("owner"), **kw))
    # a body/query attempt to name the grantor is ignored (field no longer exists)
    kw2 = {"json": {**cg["json"], "granted_by": "app_tap"}} if "json" in kw else {"params": {**cg["params"], "granted_by": "app_tap"}}
    rec("consent_owner_claims_grantor", c.post("/consent/grant", headers=H("owner"), **kw2))
if mod_name != "voice_consent_gateway.api" and mod_name != "rcap_p.api":
    rec("kill_noauth", c.post("/kill_switch", json={}))
    rec("kill_voice", c.post("/kill_switch", headers=H("voice"), json={}))
    rec("kill_auto", c.post("/kill_switch", headers=H("auto"), json={}))
if sensitive_tool:
    sb = {"tool_name": sensitive_tool, "arguments": {"entity_id": "lock.door"}, "explanation": "t"}
    rec("sens_owner_noapproval", c.post("/tools/call", headers=H("owner"), json=sb))
    rec("sens_auto", c.post("/tools/call", headers=H("auto"), json=sb))
    rec("sens_voice", c.post("/tools/call", headers=H("voice"), json=sb))
    rec("approval_request_auto", c.post("/approvals/request", headers=H("auto"), json=sb))
    r = c.post("/approvals/request", headers=H("owner"), json=sb)
    rec("approval_request_owner", r)
    aid = r.json().get("approval_id")
    rec("approval_pending_voice", c.get("/approvals/pending", headers=H("voice")))
    rec("approval_self_confirm", c.post(f"/approvals/{aid}/decision", headers=H("owner"), json={"approve": True}))
    rec("sens_owner_unconfirmed", c.post("/tools/call", headers=H("owner"), json={**sb, "approval_id": aid}))
    rec("approval_voice_confirm", c.post(f"/approvals/{aid}/decision", headers=H("voice"), json={"approve": True}))
    rec("approval_owner2_confirm", c.post(f"/approvals/{aid}/decision", headers=H("owner2"), json={"approve": True}))
    rec("sens_other_args", c.post("/tools/call", headers=H("owner"),
                                  json={**sb, "arguments": {"entity_id": "lock.garage"}, "approval_id": aid}))
    rec("sens_owner_approved", c.post("/tools/call", headers=H("owner"), json={**sb, "approval_id": aid}))
    rec("sens_owner_reuse", c.post("/tools/call", headers=H("owner"), json={**sb, "approval_id": aid}))
if mod_name in ("device_control_gateway.api", "trading_risk_engine.api"):
    rec("qap_enroll_noauth", c.post("/qap/devices/enroll", json={"device_id": "d", "role": "laptop"}))
    rec("qap_enroll_voice", c.post("/qap/devices/enroll", headers=H("voice"), json={"device_id": "d", "role": "laptop"}))
    rec("qap_enroll_owner", c.post("/qap/devices/enroll", headers=H("owner"), json={"device_id": "d", "role": "laptop"}))
    rec("qap_token_auto", c.post("/qap/vault/tokens", headers=H("auto"), json={"device_id": "d", "scope": "x"}))
    rec("qap_token_noauth", c.post("/qap/vault/tokens", json={"device_id": "d", "scope": "x"}))
    rec("qap_token_badscope", c.post("/qap/vault/tokens", headers=H("owner"), json={"device_id": "d", "scope": "Bad Scope!"}))
    rec("qap_token_longttl", c.post("/qap/vault/tokens", headers=H("owner"), json={"device_id": "d", "scope": "x", "ttl_seconds": 10 ** 7}))
    rec("qap_token_unknown_dev", c.post("/qap/vault/tokens", headers=H("owner"), json={"device_id": "ghost", "scope": "x"}))
    rec("qap_token_ok", c.post("/qap/vault/tokens", headers=H("owner"), json={"device_id": "d", "scope": "x", "ttl_seconds": 60}))
    rec("qap_chaos_auto", c.post("/qap/chaos/run", headers=H("auto"), params={"target_session_id": "s"}))
    rec("qap_session_ro", c.post("/qap/sessions", headers=H("ro"), params={"device_id": "d"}))
    rec("qap_ledger_noauth", c.get("/qap/ledger"))
    rec("qap_ledger_ro", c.get("/qap/ledger", headers=H("ro")))
print("RESULT=" + json.dumps(out, default=str))
