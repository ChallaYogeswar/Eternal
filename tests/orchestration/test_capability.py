import json

import pytest

from orchestration.capability import (CapabilityError, CapabilityIssuer, CapabilityVerifier, MAX_TOKEN_CHARS,
                                      generate_keypair)
from orchestration.consent import ConsentError, ConsentStore
from orchestration.contracts import RunStatus, ToolTier
from orchestration.identity import RESERVED_KEY, sign_envelope, verify_envelope
from tests.orchestration.helpers import FakeGateway, build, catalog, final, make_spec, task, tool_req

TIERS = {"read_doc": 0, "pub_tool": 0, "web_search": 0, "save_draft": 1, "nuke": 2}


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def setup(clock=None, **issue_kw):
    clock = clock or Clock()
    priv, pub = generate_keypair()
    issuer = CapabilityIssuer(priv, clock=clock)
    verifier = CapabilityVerifier(pub, tier_lookup=TIERS.get, clock=clock)
    kw = dict(agent="auditor", run_id="r1", session_id="s1", spec_tools=["read_doc", "pub_tool"], spec_max_tier=0,
              call_cap=3, ttl_s=60)
    kw.update(issue_kw)
    return clock, issuer, verifier, issuer.issue(**kw)


def call(verifier, token, **over):
    kw = dict(session_id="s1", tool="read_doc", source_label="agent:auditor", caller_kind="automated_routine",
              confirmation_channel="automated")
    kw.update(over)
    return verifier.verify(token, **kw)


# ------------------------------------------------------------------------------------------ tokens
def test_valid_token_is_accepted_and_counts_calls():
    _, _, v, tok = setup()
    r = call(v, tok)
    assert r.ok and r.claims.agent == "auditor" and v.calls_used(r.claims.jti) == 1


@pytest.mark.parametrize("over,reason", [
    (dict(session_id="other"), "wrong_session"),
    (dict(source_label="agent:writer"), "wrong_identity"),
    (dict(source_label="human"), "wrong_identity"),
    (dict(caller_kind="owner_interactive"), "caller_not_automated"),
    (dict(confirmation_channel="voice"), "channel_mismatch"),
    (dict(tool="web_search"), "tool_out_of_scope"),
    (dict(tool="nuke"), "tool_out_of_scope"),
])
def test_every_mismatch_is_denied_with_a_specific_reason(over, reason):
    _, _, v, tok = setup()
    r = call(v, tok, **over)
    assert not r.ok and r.reason == reason
    assert v.calls_used(r.claims.jti) == 0                       # a denial never spends a call


def test_missing_garbage_oversized_and_foreign_tokens_are_denied():
    _, issuer, v, tok = setup()
    assert call(v, None).reason == "no_token" and call(v, "").reason == "no_token"
    for junk in ("x", "a.b", "a.b.c", "....", "e30.e30", "!!!.???"):
        assert call(v, junk).reason in ("malformed", "bad_signature")
    assert call(v, "A" * (MAX_TOKEN_CHARS + 1)).reason == "malformed"
    other_priv, _ = generate_keypair()
    foreign = CapabilityIssuer(other_priv, clock=Clock()).issue(
        agent="auditor", run_id="r1", session_id="s1", spec_tools=["read_doc"], spec_max_tier=0, call_cap=1, ttl_s=60)
    assert call(v, foreign).reason == "bad_signature"


def test_tampering_with_the_claims_breaks_the_signature():
    import base64
    _, _, v, tok = setup()
    body, sig = tok.split(".")
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    claims["tools"] = ["read_doc", "pub_tool", "nuke"]
    claims["max_tier"] = 2
    forged = base64.urlsafe_b64encode(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()).rstrip(b"=").decode()
    assert call(v, f"{forged}.{sig}", tool="nuke").reason == "bad_signature"


def test_expiry_and_call_cap():
    clock, _, v, tok = setup(call_cap=2)
    assert call(v, tok).ok and call(v, tok).ok
    assert call(v, tok).reason == "call_cap_exhausted"
    clock.t += 61
    assert call(v, tok).reason == "expired"


def test_tier_is_checked_against_the_real_tier_of_the_tool():
    _, _, v, tok = setup(spec_tools=["read_doc", "save_draft"], spec_max_tier=0)
    assert call(v, tok, tool="save_draft").reason == "tier_exceeded"
    _, _, v2, tok2 = setup(spec_tools=["save_draft"], spec_max_tier=1)
    assert call(v2, tok2, tool="save_draft").ok
    _, _, v3, tok3 = setup(spec_tools=["mystery"], spec_max_tier=2)
    assert call(v3, tok3, tool="mystery").reason == "unknown_tool"


def test_issue_attenuates_to_spec_intersect_plan_and_lower_tier():
    _, _, v, tok = setup(spec_tools=["read_doc", "pub_tool", "save_draft"], spec_max_tier=1,
                         plan_tools=["read_doc", "nuke"], plan_max_tier=0)
    assert call(v, tok).ok
    assert call(v, tok, tool="pub_tool").reason == "tool_out_of_scope"       # not in the plan subset
    assert call(v, tok, tool="nuke").reason == "tool_out_of_scope"           # plan cannot ADD to the spec


def test_issue_rejects_bad_input():
    _, issuer, _, _ = setup()
    base = dict(agent="a", run_id="r", session_id="s", spec_tools=[], spec_max_tier=0, call_cap=1, ttl_s=10)
    for bad in (dict(ttl_s=0), dict(ttl_s=10 ** 6), dict(call_cap=-1), dict(origin_channel="carrier-pigeon"),
                dict(agent=""), dict(session_id="")):
        with pytest.raises(CapabilityError):
            issuer.issue(**{**base, **bad})


def test_voice_origin_token_only_works_with_the_voice_channel():            # I10 / A5
    _, _, v, tok = setup(origin_channel="voice")
    assert call(v, tok, confirmation_channel="voice").ok
    assert call(v, tok, confirmation_channel="automated").reason == "channel_mismatch"


# --------------------------------------------------------------------------------------- revocation
def test_revoking_a_run_a_token_or_everything():
    _, issuer, v, tok = setup()
    assert call(v, tok).ok
    v.revoke_run("r1")
    assert call(v, tok).reason == "revoked"
    _, _, v2, tok2 = setup()
    v2.revoke_token(call(v2, tok2).claims.jti)
    assert call(v2, tok2).reason == "revoked"


def test_kill_switch_voids_every_existing_token_but_new_ones_work():
    clock, issuer, v, tok = setup()
    clock.t += 5
    v.revoke_all()
    assert call(v, tok).reason == "revoked"
    clock.t += 1
    fresh = issuer.issue(agent="auditor", run_id="r2", session_id="s1", spec_tools=["read_doc"], spec_max_tier=0,
                         call_cap=1, ttl_s=60)
    assert call(v, fresh).ok


def test_a_restarted_verifier_refuses_tokens_issued_before_it_started():
    clock = Clock()
    priv, pub = generate_keypair()
    tok = CapabilityIssuer(priv, clock=clock).issue(agent="a", run_id="r", session_id="s", spec_tools=["read_doc"],
                                                    spec_max_tier=0, call_cap=5, ttl_s=600)
    clock.t += 10
    restarted = CapabilityVerifier(pub, tier_lookup=TIERS.get, clock=clock)
    r = restarted.verify(tok, session_id="s", tool="read_doc", source_label="agent:a",
                         caller_kind="automated_routine", confirmation_channel="automated")
    assert r.reason == "revoked"                       # counters reset on restart, so old budgets must not revive


# ------------------------------------------------------------------------------------- delegation
def test_child_tokens_can_only_narrow_and_die_with_the_root_run():
    clock, issuer, v, parent = setup(spec_tools=["read_doc", "pub_tool"], call_cap=5, ttl_s=100)
    child = issuer.attenuate(parent, agent="researcher", run_id="r1.c1", tools=["read_doc"], call_cap=2, ttl_s=500)
    r = call(v, child, source_label="agent:researcher")
    assert r.ok and r.claims.root_run_id == "r1" and r.claims.exp == r.claims.iat + 100   # expiry capped to parent
    assert call(v, child, source_label="agent:researcher", tool="pub_tool").reason == "tool_out_of_scope"
    for bad in (dict(tools=["read_doc", "nuke"]), dict(tools=["read_doc"], call_cap=99),
                dict(tools=["read_doc"], max_tier=2)):
        with pytest.raises(CapabilityError):
            issuer.attenuate(parent, agent="x", run_id="r1.c2", **{"call_cap": 1, "ttl_s": 10, **bad})
    v.revoke_run("r1")
    assert call(v, child, source_label="agent:researcher").reason == "revoked"


def test_cannot_attenuate_an_expired_or_forged_parent():
    clock, issuer, v, parent = setup(ttl_s=10)
    clock.t += 11
    with pytest.raises(CapabilityError):
        issuer.attenuate(parent, agent="x", run_id="c", tools=["read_doc"], call_cap=1, ttl_s=5)
    with pytest.raises(CapabilityError):
        issuer.attenuate("garbage.token", agent="x", run_id="c", tools=[], call_cap=0, ttl_s=5)


# ----------------------------------------------------------------------------- identity envelope
def test_identity_envelope_roundtrip_and_binding():
    priv, pub = generate_keypair()
    clock = Clock()
    args = {"title": "t", "n": 1}
    env = sign_envelope(priv, identity="agent:writer", run_id="r1", tool="drafts_save", arguments=args, clock=clock)
    sent = {**args, RESERVED_KEY: env}
    ok, reason, body = verify_envelope(pub, sent, tool="drafts_save", clock=clock)
    assert ok and body["identity"] == "agent:writer" and body["run_id"] == "r1"
    assert verify_envelope(pub, sent, tool="tasks_create", clock=clock)[1] == "identity_for_other_tool"
    assert verify_envelope(pub, {**sent, "n": 2}, tool="drafts_save", clock=clock)[1] == "identity_for_other_arguments"
    assert verify_envelope(pub, args, tool="drafts_save", clock=clock)[1] == "missing_identity"
    clock.t += 31
    assert verify_envelope(pub, sent, tool="drafts_save", clock=clock)[1] == "identity_expired"


def test_identity_envelope_forgery_is_rejected():
    priv, pub = generate_keypair()
    other, _ = generate_keypair()
    clock = Clock()
    forged = sign_envelope(other, identity="human:owner", run_id="r", tool="tasks_update", arguments={}, clock=clock)
    assert verify_envelope(pub, {RESERVED_KEY: forged}, tool="tasks_update", clock=clock)[1] == "bad_identity_signature"
    real = sign_envelope(priv, identity="agent:task_issuer", run_id="r", tool="tasks_update", arguments={}, clock=clock)
    real["identity"] = "human:owner"                         # upgrade attempt: agent -> human
    assert verify_envelope(pub, {RESERVED_KEY: real}, tool="tasks_update", clock=clock)[1] == "bad_identity_signature"
    for junk in (None, "x", [], {"sig": "zz"}, {"identity": 1}):
        assert not verify_envelope(pub, {RESERVED_KEY: junk}, tool="t", clock=clock)[0]


# ----------------------------------------------------------------------------------------- consent
def test_consent_needs_a_non_voice_non_automated_channel():
    c = ConsentStore(clock=Clock())
    for via in ("voice", "automated", "", "agent"):
        with pytest.raises(ConsentError):
            c.approve("r1", "save_draft", via=via)
    c.approve("r1", "save_draft", via="app_tap")
    assert c.check("save_draft", "r1") == "granted"


def test_consent_is_pending_by_default_consumed_expiring_and_per_run():
    clock = Clock()
    c = ConsentStore(clock=clock)
    assert c.check("save_draft", "r1") == "pending" and c.pending() == [("r1", "save_draft")]
    c.approve("r1", "save_draft", via="text_typed", max_calls=2, ttl_s=60)
    assert c.pending() == []
    assert c.check("save_draft", "r1") == "granted" and c.check("save_draft", "r1") == "granted"
    assert c.check("save_draft", "r1") == "pending"                    # uses exhausted
    assert c.check("save_draft", "r2") == "pending"                    # another run is not covered
    c.approve("r1", "save_draft", via="text_typed", ttl_s=10)
    clock.t += 11
    assert c.check("save_draft", "r1") == "pending"                    # expired
    c.deny("r1", "save_draft")
    assert c.check("save_draft", "r1") == "denied"
    c.approve("r3", "save_draft", via="app_tap")
    c.revoke_run("r3")
    assert c.check("save_draft", "r3") == "pending"
    with pytest.raises(ConsentError):
        c.approve("r1", "save_draft", via="app_tap", max_calls=0)


# --------------------------------------------------------- broker + runner wiring with a verifying chain
class VerifyingChain:
    """Test double for the composition root's wrapper: verifies the token, then 'executes'."""
    def __init__(self, verifier):
        self.verifier, self.calls, self.denials = verifier, [], []

    def call_tool(self, **kw):
        r = self.verifier.verify(kw.get("capability_token"), session_id=kw["session_id"], tool=kw["tool_name"],
                                 source_label=kw["source_label"], caller_kind=kw["caller_kind"],
                                 confirmation_channel=kw["confirmation_channel"])
        if not r.ok:
            self.denials.append(r.reason)
            return {"ok": False, "content": None, "denied_reason": f"capability:{r.reason}"}
        self.calls.append(kw)
        return {"ok": True, "content": {"data": "x"}}


class Port:
    def __init__(self, issuer, *, raises=False, **issue_over):
        self.issuer, self.raises, self.issue_over, self.handles = issuer, raises, issue_over, []

    def begin(self, *, spec, task, session_id, origin_channel):
        if self.raises:
            raise RuntimeError("issuer down")
        kw = dict(agent=spec.name, run_id=task.run_id, session_id=session_id, spec_tools=spec.allowed_tools,
                  spec_max_tier=int(spec.max_tool_tier), call_cap=5, ttl_s=60, origin_channel=origin_channel)
        kw.update(self.issue_over)
        h = Handle(self.issuer.issue(**kw))
        self.handles.append(h)
        return h


class Handle:
    def __init__(self, token):
        self.token, self.closed = token, 0

    def rotate_token(self, tool):
        return None

    def summary(self):
        return {"tools": ["x"], "call_cap": 5}

    def close(self):
        self.closed += 1


def wired(tmp_path, replies, spec=None, **port_kw):
    clock = Clock(1000.0)
    priv, pub = generate_keypair()
    issuer = CapabilityIssuer(priv, clock=clock)
    verifier = CapabilityVerifier(pub, tier_lookup=lambda t: int(catalog()[t].tier) if t in catalog() else None, clock=clock)
    chain = VerifyingChain(verifier)
    spec = spec or make_spec(("read_doc", "pub_tool"), name="tester")
    runner, local, gw, ledger = build(tmp_path, spec, replies, gateway=chain)
    port = Port(issuer, **port_kw)
    runner.capability = port
    return runner, local, chain, ledger, port, verifier


def test_token_reaches_the_chain_is_never_shown_to_the_model_and_never_ledgered(tmp_path):
    runner, local, chain, ledger, port, _ = wired(tmp_path, [tool_req("read_doc", {"path": "a"}), final({"answer": "ok"})])
    res = runner.run(task(), "s1")
    assert res.status == RunStatus.OK and len(chain.calls) == 1 and chain.calls[0]["capability_token"]
    token = port.handles[0].token
    assert all(token not in json.dumps(c["messages"]) and token not in c["system"] for c in local.calls)
    assert all(token not in json.dumps(e, default=str) for e in ledger.recent(100))
    started = [e for e in ledger.recent(100) if e["event_type"] == "agent_run_started"][0]
    assert started["payload"]["capability"] == {"tools": ["x"], "call_cap": 5}
    assert port.handles[0].closed == 1                                    # authority ended with the run


def test_a_tool_outside_the_token_is_denied_by_the_verifier_even_if_the_broker_would_allow_it(tmp_path):
    runner, _, chain, _, _, _ = wired(tmp_path, [tool_req("pub_tool"), final({"answer": "ok"})],
                                      plan_tools=["read_doc"])              # plan subset excludes pub_tool
    res = runner.run(task(), "s1")
    assert res.status == RunStatus.OK and chain.calls == [] and chain.denials == ["tool_out_of_scope"]


def test_call_cap_in_the_token_ends_the_run_with_denials_not_extra_calls(tmp_path):
    replies = [tool_req("read_doc", {"path": str(i)}) for i in range(6)] + [final({"answer": "ok"})]
    runner, _, chain, _, _, _ = wired(tmp_path, replies, call_cap=2)
    res = runner.run(task(), "s1")
    assert len(chain.calls) == 2 and "call_cap_exhausted" in chain.denials
    assert res.status in (RunStatus.DENIED_OUT, RunStatus.OK, RunStatus.BUDGET_EXHAUSTED)


def test_no_authority_means_no_run_and_no_tool_call(tmp_path):
    runner, local, chain, _, port, _ = wired(tmp_path, [tool_req("read_doc", {"path": "a"})], raises=True)
    res = runner.run(task(), "s1")
    assert res.status == RunStatus.REFUSED and res.detail.startswith("capability_unavailable")
    assert chain.calls == [] and local.calls == [] and port.handles == []


def test_authority_is_closed_even_when_the_session_is_killed_mid_run(tmp_path):
    alive = {"v": True}

    def flip(n):
        alive["v"] = False

    runner, local, chain, _, port, _ = wired(tmp_path, [tool_req("read_doc", {"path": "a"})])
    runner._alive = lambda: alive["v"]
    local.on_call = flip
    res = runner.run(task(), "s1")
    assert res.status == RunStatus.KILLED and port.handles[0].closed == 1 and chain.calls == []


def test_revoking_all_tokens_stops_the_next_tool_call(tmp_path):
    runner, local, chain, _, _, verifier = wired(
        tmp_path, [tool_req("read_doc", {"path": "a"}), tool_req("read_doc", {"path": "b"}), final({"answer": "ok"})])
    clock = verifier.clock

    def kill_after_first(n):
        if n == 2:
            clock.t += 1
            verifier.revoke_all()
    local.on_call = kill_after_first
    runner.run(task(), "s1")
    assert len(chain.calls) == 1 and chain.denials == ["revoked"]


def test_missing_token_at_the_broker_is_denied_before_dispatch(tmp_path):
    runner, _, chain, _, port, _ = wired(tmp_path, [tool_req("read_doc", {"path": "a"}), final({"answer": "ok"})])
    orig = port.begin

    def begin(**kw):
        h = orig(**kw)
        h.token = ""
        return h
    port.begin = begin
    res = runner.run(task(), "s1")
    assert res.status == RunStatus.OK and chain.calls == [] and chain.denials == []


def test_voice_origin_run_carries_the_voice_channel_and_still_verifies(tmp_path):
    runner, _, chain, _, _, _ = wired(tmp_path, [tool_req("read_doc", {"path": "a"}), final({"answer": "ok"})])
    res = runner.run(task(origin="voice"), "s1")
    assert res.status == RunStatus.OK and chain.calls[0]["confirmation_channel"] == "voice"


def test_reserved_argument_keys_are_still_rejected_so_an_agent_cannot_forge_an_identity(tmp_path):
    runner, _, chain, _, _, _ = wired(
        tmp_path, [tool_req("read_doc", {"path": "a", "_identity": {"identity": "human:owner"}}), final({"answer": "ok"})])
    runner.run(task(), "s1")
    assert chain.calls == []
