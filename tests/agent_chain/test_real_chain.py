"""Agent calls through the REAL Phase 6 -> 5 -> 4 -> RCAP chain with the capability layer in front."""
import json
import os
import sqlite3

import pytest

from agent_chain.gateway import CapabilityGateway
from agent_chain.request import AgentCallRequest
from orchestration.contracts import ToolTier
from orchestration.identity import RESERVED_KEY, verify_envelope
from voice_consent_gateway.models import ConfirmationChannel, WriteCallRequest
from tests.orchestration.helpers import make_spec, task


def spec_for(tools, name="researcher", write=False):
    return make_spec(tools, name=name, can_write=write, tier=ToolTier.WRITE if write else ToolTier.READ_ONLY)


def begin(w, sid, tools, *, name="researcher", run_id="r1", write=False):
    return w.port.begin(spec=spec_for(tools, name, write), task=task(run_id=run_id), session_id=sid, origin_channel="typed")


def call(w, sid, h, tool, args=None, *, name="researcher", token="__use_handle__", rotate="__use_handle__", **kw):
    return w.core.call_tool(
        session_id=sid, tool_name=tool, arguments=args if args is not None else {"query": "q", "entity_id": "light.x"},
        source_label=f"agent:{name}", explanation="test",
        capability_token=h.token if token == "__use_handle__" else token,
        rotate_token=h.rotate_token(tool) if rotate == "__use_handle__" else rotate, **kw)


def denied_entries(w):
    return [r for r in w.real.ledger.recent(500) if r["event_type"] == "agent_capability_denied"] if hasattr(w.real.ledger, "recent") else []


def all_ledger_text(w):
    path = os.path.join(w.real.orch.config.data_dir, "ledger.db")
    con = sqlite3.connect(path)
    try:
        return json.dumps([list(r) for r in con.execute("select * from ledger")], default=str)
    except sqlite3.Error:
        names = [r[0] for r in con.execute("select name from sqlite_master where type='table'")]
        return json.dumps({n: [list(r) for r in con.execute(f"select * from {n}")] for n in names}, default=str)
    finally:
        con.close()


def test_read_only_call_flows_through_all_four_phases(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"])
    r = call(wired, sid, h, "web_search")
    assert r["ok"] is True
    assert wired.real.executor.calls == [("web_search", {"query": "q", "entity_id": "light.x"})]
    # the inner chain's own ledger saw the call: it was not bypassed
    assert "web_search" in all_ledger_text(wired)


def test_inner_chain_receives_plain_request_without_token(wired):
    seen = []
    inner = wired.gateway.inner
    orig = inner.call_tool

    async def spy(request):
        seen.append(request)
        return await orig(request)
    inner.call_tool = spy
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"])
    call(wired, sid, h, "web_search")
    assert len(seen) == 1
    assert type(seen[0]) is WriteCallRequest and not hasattr(seen[0], "capability_token")
    assert seen[0].source_label == "agent:researcher"
    assert seen[0].confirmation_channel == ConfirmationChannel.AUTOMATED


def test_token_never_reaches_any_ledger(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search", "laptop_lock"])
    call(wired, sid, h, "web_search")
    call(wired, sid, h, "laptop_lock")                         # denied by the verifier (tier): logged
    call(wired, sid, h, "web_search", token="garbage")         # denied: logged
    assert h.token not in all_ledger_text(wired)
    assert h.token not in json.dumps(h.summary())


def test_plain_write_call_request_is_refused(wired):
    sid = wired.real.new_session()
    req = WriteCallRequest(tool_name="web_search", arguments={"query": "q"}, session_id=sid,
                           confirmation_channel=ConfirmationChannel.AUTOMATED, source_label="agent:researcher",
                           explanation="t", caller_kind="automated_routine")
    import asyncio
    res = asyncio.run(wired.gateway.call_tool(req))
    assert not res.ok and "not_an_agent_call_request" in res.denied_reason
    assert wired.real.executor.calls == []


def test_missing_and_forged_tokens_are_denied_before_the_chain(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"])
    for tok in (None, "", "abc.def", h.token[:-4] + "AAAA"):
        r = call(wired, sid, h, "web_search", token=tok)
        assert r["ok"] is False and r["denied_reason"].startswith("capability:")
    assert wired.real.executor.calls == []


def test_tool_outside_the_agents_spec_is_denied(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"])
    r = call(wired, sid, h, "ha_get_state", rotate=None)
    assert not r["ok"] and "tool_out_of_scope" in r["denied_reason"]
    assert wired.real.executor.calls == []


def test_tier_is_taken_from_the_registry_not_from_the_agent(wired):
    sid = wired.real.new_session()
    # a READ_ONLY-tier spec that (wrongly) lists a WRITE and a SENSITIVE tool: the verifier denies both on tier
    h = begin(wired, sid, ["laptop_lock", "laptop_unlock", "web_search"])
    for tool in ("laptop_lock", "laptop_unlock"):
        r = call(wired, sid, h, tool)
        assert not r["ok"] and "tier_exceeded" in r["denied_reason"], r
    assert wired.real.executor.calls == []


def test_write_tool_still_needs_the_chains_own_consent(wired):
    """The capability layer says 'this agent may call it'; Phase 4's per-tool consent still decides separately."""
    sid = wired.real.new_session()
    h = begin(wired, sid, ["laptop_lock"], name="taskissuer", write=True)
    r = call(wired, sid, h, "laptop_lock", name="taskissuer")
    assert not r["ok"] and "requires consent" in r["denied_reason"]
    assert wired.real.executor.calls == []


def test_call_cap_is_enforced(wired):
    sid = wired.real.new_session()
    spec = make_spec(["web_search"], name="researcher")
    from orchestration.contracts import Budget
    spec = make_spec(["web_search"], name="researcher", budget=Budget(max_tool_calls=2))
    h = wired.port.begin(spec=spec, task=task(), session_id=sid, origin_channel="typed")
    assert call(wired, sid, h, "web_search")["ok"]
    assert call(wired, sid, h, "web_search")["ok"]
    r = call(wired, sid, h, "web_search")
    assert not r["ok"] and "call_cap_exhausted" in r["denied_reason"]
    assert len(wired.real.executor.calls) == 2


def test_token_is_bound_to_its_session(wired):
    a, b = wired.real.new_session("dev-a"), wired.real.new_session("dev-b")
    h = begin(wired, a, ["web_search"])
    r = call(wired, b, h, "web_search", rotate=None)
    assert not r["ok"] and "wrong_session" in r["denied_reason"]


def test_token_is_bound_to_the_agent_identity(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"], name="researcher")
    r = call(wired, sid, h, "web_search", name="writer")        # another agent presents researcher's token
    assert not r["ok"] and "wrong_identity" in r["denied_reason"]


def test_non_automated_channel_is_refused_for_agents(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"])
    r = call(wired, sid, h, "web_search", confirmation_channel="text_typed", caller_kind="owner_interactive")
    assert not r["ok"]
    assert wired.real.executor.calls == []


def test_closing_the_run_revokes_the_token(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"])
    assert call(wired, sid, h, "web_search")["ok"]
    h.close()
    r = call(wired, sid, h, "web_search")
    assert not r["ok"] and "revoked" in r["denied_reason"]


def test_close_also_ends_the_runs_consent_grants(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["laptop_lock"], write=True, name="taskissuer", run_id="rc")
    wired.consent.approve("rc", "laptop_lock", via="app_tap")
    h.close()
    assert wired.consent.check("laptop_lock", "rc") == "pending"


def test_reserved_arguments_from_the_agent_are_stripped_and_identity_is_genuine(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["ha_get_state"], name="auditor")
    forged = {"identity": "human:owner", "run_id": "r1", "tool": "ha_get_state", "args": "0" * 64, "exp": 9e12, "sig": "AAAA"}
    r = call(wired, sid, h, "ha_get_state", {"entity_id": "light.x", RESERVED_KEY: forged, "_other": 1}, name="auditor")
    assert r["ok"]
    (tool, args), = wired.real.executor.calls
    assert "_other" not in args
    ok, reason, body = verify_envelope(wired.ident_pub, args, tool=tool, clock=wired.clock)
    assert ok, reason
    assert body["identity"] == "agent:auditor"           # not the forged human identity


def test_identity_envelope_only_for_configured_tools(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"])
    call(wired, sid, h, "web_search")
    assert RESERVED_KEY not in wired.real.executor.calls[0][1]


def test_identity_tools_require_a_key(wired):
    with pytest.raises(ValueError):
        CapabilityGateway(wired.real.p6, wired.verifier, ledger=wired.real.ledger, identity_tools=frozenset({"x"}))


def test_kill_switch_revokes_agent_authority_and_quarantines_sessions(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"])
    assert call(wired, sid, h, "web_search")["ok"]
    wired.gateway.kill(reason="test")
    r = call(wired, sid, h, "web_search")
    assert not r["ok"]
    assert wired.real.orch.get_session_state(sid).value != "ACTIVE"
    assert "revoked" in r["denied_reason"]              # refused by the capability layer itself, not just downstream
    assert len(wired.real.executor.calls) == 1          # nothing reached the tools after the kill


def test_quarantined_session_cannot_start_new_agent_work(wired):
    sid = wired.real.new_session()
    wired.gateway.kill(reason="test")
    with pytest.raises(ValueError, match="not ACTIVE"):
        begin(wired, sid, ["web_search"])


def test_token_issued_before_a_verifier_restart_is_void(wired):
    from orchestration.capability import CapabilityVerifier
    from agent_chain.port import tier_lookup_from_registry
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"])
    wired.clock.t += 5
    fresh = CapabilityVerifier(wired.pub, tier_lookup=tier_lookup_from_registry(wired.real.registry), clock=wired.clock)
    wired.gateway.verifier = fresh
    r = call(wired, sid, h, "web_search")
    assert not r["ok"]


def test_denials_are_logged_with_run_agent_and_reason(wired):
    sid = wired.real.new_session()
    h = begin(wired, sid, ["web_search"], run_id="rlog")
    call(wired, sid, h, "ha_get_state", rotate=None)
    text = all_ledger_text(wired)
    assert "agent_capability_denied" in text and "tool_out_of_scope" in text and "rlog" in text


def test_orchestration_and_core_never_import_agent_chain():
    import pathlib
    root = pathlib.Path(__file__).resolve().parents[2]
    for pkg in ("orchestration", "eternal_core"):
        for f in (root / pkg).rglob("*.py"):
            assert "agent_chain" not in f.read_text(encoding="utf-8"), f
