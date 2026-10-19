import json

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from api_auth.approvals import ApprovalError, ApprovalStore
from api_auth.fastapi_deps import require, set_registry
from api_auth.identity import IdentityRefused, derive_call_identity
from api_auth.principals import AuthRegistry, ROLES, hash_token, new_token


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def registry(**kw):
    toks = {r: new_token() for r in ("owner_app", "voice_client", "automation", "readonly")}
    entries = [{"id": f"{r}1", "role": r, "token_sha256": hash_token(t)} for r, t in toks.items()]
    entries.append({"id": "owner2", "role": "owner_app", "token_sha256": hash_token(new_token())})
    return AuthRegistry(entries, **kw), toks


def principal(reg, toks, role):
    return reg.authenticate(toks[role])


# ---- registry ----------------------------------------------------------------------------------
def test_valid_token_maps_to_principal_and_unknown_does_not():
    reg, toks = registry()
    assert reg.authenticate(toks["owner_app"]).id == "owner_app1"
    assert reg.authenticate("etn_wrong") is None and reg.authenticate(None) is None and reg.authenticate("") is None


def test_store_never_holds_raw_tokens():
    reg, toks = registry()
    assert all(t not in json.dumps(list(reg._by_hash)) for t in toks.values())


def test_bad_config_is_rejected():
    with pytest.raises(ValueError):
        AuthRegistry([{"id": "a", "role": "root", "token_sha256": "0" * 64}])
    with pytest.raises(ValueError):
        AuthRegistry([{"id": "a", "role": "owner_app", "token_sha256": "short"}])
    h = hash_token("x")
    with pytest.raises(ValueError):
        AuthRegistry([{"id": "a", "role": "owner_app", "token_sha256": h}, {"id": "a", "role": "readonly", "token_sha256": h}])


def test_failed_attempts_are_throttled_per_client():
    clk = Clock()
    reg, _ = registry(max_failures=3, window_s=60, clock=clk)
    for _ in range(3):
        reg.authenticate("bad", "1.2.3.4")
    assert reg.throttled("1.2.3.4") and not reg.throttled("5.6.7.8")
    clk.t += 61
    assert not reg.throttled("1.2.3.4")


def test_roles_are_least_privilege():
    assert ROLES["voice_client"].channels == {"voice"}
    assert "consent:write" not in ROLES["voice_client"].scopes and "approvals:confirm" not in ROLES["voice_client"].scopes
    assert "approvals:confirm" not in ROLES["automation"].scopes and "kill:trigger" not in ROLES["automation"].scopes
    assert ROLES["readonly"].channels == frozenset() and "tools:call" not in ROLES["readonly"].scopes


# ---- identity ----------------------------------------------------------------------------------
def test_channel_and_caller_kind_come_from_principal():
    reg, toks = registry()
    assert derive_call_identity(principal(reg, toks, "owner_app")) == ("app_tap", "owner_interactive")
    assert derive_call_identity(principal(reg, toks, "voice_client")) == ("voice", "owner_interactive")
    assert derive_call_identity(principal(reg, toks, "automation")) == ("automated", "automated_routine")


@pytest.mark.parametrize("role,channel,kind", [
    ("voice_client", "text_typed", None), ("voice_client", "app_tap", None), ("automation", "app_tap", None),
    ("automation", None, "owner_interactive"), ("voice_client", None, "automated_routine")])
def test_body_cannot_elevate_channel_or_caller_kind(role, channel, kind):
    reg, toks = registry()
    with pytest.raises(IdentityRefused):
        derive_call_identity(principal(reg, toks, role), requested_channel=channel, requested_caller_kind=kind)


def test_owner_may_narrow_to_typed_and_readonly_cannot_call():
    reg, toks = registry()
    assert derive_call_identity(principal(reg, toks, "owner_app"), requested_channel="text_typed")[0] == "text_typed"
    with pytest.raises(IdentityRefused):
        derive_call_identity(principal(reg, toks, "readonly"))


# ---- approvals ---------------------------------------------------------------------------------
def setup_approval(clk=None, role="automation"):
    reg, toks = registry()
    store = ApprovalStore(clock=clk or Clock())
    req = principal(reg, toks, role)
    a = store.request(req, session_id="s1", tool_name="ha_unlock", arguments={"entity_id": "lock.door"})
    return reg, toks, store, req, a


def consume(store, a, who, **over):
    kw = dict(session_id="s1", tool_name="ha_unlock", arguments={"entity_id": "lock.door"})
    kw.update(over)
    return store.consume(a.id, who, **kw)


def test_approval_happy_path_is_single_use():
    reg, toks, store, req, a = setup_approval()
    store.confirm(a.id, principal(reg, toks, "owner_app"))
    assert consume(store, a, req)[0] is True
    assert consume(store, a, req) == (False, "approval already used")


def test_unconfirmed_missing_and_unknown_are_refused():
    reg, toks, store, req, a = setup_approval()
    assert consume(store, a, req)[0] is False
    assert store.consume(None, req, session_id="s1", tool_name="ha_unlock", arguments={})[0] is False
    assert store.consume("nope", req, session_id="s1", tool_name="ha_unlock", arguments={})[0] is False


@pytest.mark.parametrize("over", [{"session_id": "s2"}, {"tool_name": "ha_lock"},
                                  {"arguments": {"entity_id": "lock.garage"}}])
def test_approval_is_bound_to_session_tool_and_exact_arguments(over):
    reg, toks, store, req, a = setup_approval()
    store.confirm(a.id, principal(reg, toks, "owner_app"))
    ok, why = consume(store, a, req, **over)
    assert not ok and "different" in why


def test_requester_cannot_confirm_its_own_and_non_humans_cannot_confirm():
    reg, toks, store, req, a = setup_approval(role="owner_app")
    with pytest.raises(ApprovalError, match="own"):
        store.confirm(a.id, req)
    for role in ("voice_client", "automation", "readonly"):
        with pytest.raises(ApprovalError, match="human channel"):
            store.confirm(a.id, principal(reg, toks, role))


def test_approval_belongs_to_the_requesting_caller():
    reg, toks, store, req, a = setup_approval()
    store.confirm(a.id, principal(reg, toks, "owner_app"))
    other = principal(reg, toks, "voice_client")
    assert consume(store, a, other) == (False, "approval belongs to a different caller")


def test_approval_expires_and_denial_sticks():
    clk = Clock()
    reg, toks, store, req, a = setup_approval(clk)
    store.confirm(a.id, principal(reg, toks, "owner_app"))
    clk.t += 500
    assert consume(store, a, req)[0] is False
    reg, toks, store, req, b = setup_approval()
    store.confirm(b.id, principal(reg, toks, "owner_app"), approve=False)
    assert consume(store, b, req) == (False, "approval was denied")


def test_pending_list_shows_what_a_human_would_approve():
    reg, toks, store, req, a = setup_approval()
    (row,) = store.pending()
    assert row["tool_name"] == "ha_unlock" and "lock.door" in row["summary"] and row["requester"] == req.id
    store.confirm(a.id, principal(reg, toks, "owner_app"))
    assert store.pending() == []


def test_ttl_and_pending_caps():
    reg, toks = registry()
    s = ApprovalStore(max_pending=2)
    p = principal(reg, toks, "automation")
    with pytest.raises(ApprovalError):
        s.request(p, session_id=None, tool_name="t", arguments={}, ttl_s=99999)
    s.request(p, session_id=None, tool_name="t", arguments={}); s.request(p, session_id=None, tool_name="t", arguments={})
    with pytest.raises(ApprovalError, match="too many"):
        s.request(p, session_id=None, tool_name="t", arguments={})


# ---- fastapi dependency ------------------------------------------------------------------------
def make_app():
    app = FastAPI()

    @app.get("/open")
    def open_():
        return {"ok": True}

    @app.post("/admin")
    def admin(p=Depends(require("qap:admin"))):
        return {"who": p.id}
    return TestClient(app)


def auth(tok):
    return {"Authorization": f"Bearer {tok}"}


def test_fail_closed_when_nothing_configured():
    set_registry(AuthRegistry([]))
    try:
        c = make_app()
        assert c.get("/open").status_code == 200
        r = c.post("/admin", headers=auth("anything"))
        assert r.status_code == 503
    finally:
        set_registry(None)


def test_401_403_and_success():
    reg, toks = registry()
    set_registry(reg)
    try:
        c = make_app()
        assert c.post("/admin").status_code == 401
        assert c.post("/admin", headers=auth("etn_nope")).status_code == 401
        assert c.post("/admin", headers={"Authorization": "Basic abc"}).status_code == 401
        assert c.post("/admin", headers=auth(toks["voice_client"])).status_code == 403
        assert c.post("/admin", headers=auth(toks["automation"])).status_code == 403
        r = c.post("/admin", headers=auth(toks["owner_app"]))
        assert r.status_code == 200 and r.json() == {"who": "owner_app1"}
    finally:
        set_registry(None)


def test_http_throttle_returns_429():
    reg, toks = registry(max_failures=3)
    set_registry(reg)
    try:
        c = make_app()
        for _ in range(3):
            assert c.post("/admin", headers=auth("bad")).status_code == 401
        assert c.post("/admin", headers=auth(toks["owner_app"])).status_code == 429
    finally:
        set_registry(None)
