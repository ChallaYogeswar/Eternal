import time

import pytest

from orchestration.broker import (BrokerFactory, NoWriteConsent, StaticConsent, ToolBroker, normalize_verdict,
                                  validate_spec_against_catalog)
from orchestration.contracts import Budget, RunStatus, SpecError, ToolTier
from orchestration.evidence import EvidenceRegistry
from tests.orchestration.helpers import FakeGateway, catalog, make_spec


def mk(spec=None, gw=None, **kw):
    spec = spec or make_spec(("read_doc", "pub_tool"))
    gw = gw or FakeGateway()
    ev = EvidenceRegistry()
    b = ToolBroker(spec=spec, catalog=catalog(), gateway=gw, session_id="S1", run_id="R1", evidence=ev, **kw)
    return b, gw, ev


def test_disallowed_tool_denied_without_touching_gateway():
    b, gw, _ = mk()
    o = b.execute("web_search", {}, "r")
    assert o.kind == "denied" and "tool_not_allowed" in o.verdict and gw.calls == []


def test_identity_stamping_is_fixed_and_run_id_only_in_explanation():
    b, gw, _ = mk(rotate_token_provider=lambda t: "ROTATE-SECRET")
    o = b.execute("read_doc", {"path": "a.py"}, "need it")
    k = gw.calls[0]
    assert k["source_label"] == "agent:tester"              # stable per agent, NOT per run (amendment A1)
    assert k["caller_kind"] == "automated_routine" and k["confirmation_channel"] == "automated"
    assert k["explanation"].startswith("[run R1] ") and k["session_id"] == "S1" and k["rotate_token"] == "ROTATE-SECRET"
    assert "ROTATE-SECRET" not in o.observation             # tokens never reach model context


def test_voice_origin_keeps_voice_channel_so_the_voice_gate_stays_on():
    b, gw, _ = mk(origin_channel="voice")
    b.execute("pub_tool", {}, "r")
    assert gw.calls[0]["confirmation_channel"] == "voice"


@pytest.mark.parametrize("args", [{"_identity": "forged"}, {"path": "a", "_x": 1}])
def test_reserved_argument_keys_rejected(args):
    b, gw, _ = mk()
    assert "reserved_argument_key" in b.execute("read_doc", args, "r").verdict and gw.calls == []


def test_argument_size_and_schema_validation():
    b, gw, _ = mk(make_spec(("read_doc",), budget=Budget(max_arg_bytes=50)))
    assert "arguments_too_large" in b.execute("read_doc", {"path": "x" * 200}, "r").verdict
    b2, gw2, _ = mk()
    assert "invalid_arguments" in b2.execute("read_doc", {"nope": 1}, "r").verdict and gw2.calls == []


def test_repeat_cap_blocks_identical_calls():
    b, gw, _ = mk(make_spec(("pub_tool",), budget=Budget(max_repeat_calls=2, max_denials=9)))
    for _ in range(2):
        assert b.execute("pub_tool", {"a": 1}, "r").kind == "ok"
    assert "repeat_call_blocked" in b.execute("pub_tool", {"a": 1}, "r").verdict
    assert b.execute("pub_tool", {"a": 2}, "r").kind == "ok"        # different args still fine


def test_denial_cap_ends_run_as_denied_out():
    b, _, _ = mk(make_spec(("pub_tool",), budget=Budget(max_denials=2)))
    assert b.execute("nuke", {}, "r").kind == "denied"
    o = b.execute("nuke", {}, "r")
    assert o.kind == "terminal" and o.terminal_status == RunStatus.DENIED_OUT


def test_tool_call_budget_terminal():
    b, _, _ = mk(make_spec(("pub_tool",), budget=Budget(max_tool_calls=2, max_repeat_calls=9)))
    b.execute("pub_tool", {"i": 1}, "r"); b.execute("pub_tool", {"i": 2}, "r")
    o = b.execute("pub_tool", {"i": 3}, "r")
    assert o.terminal_status == RunStatus.BUDGET_EXHAUSTED


def test_write_tool_awaits_confirmation_by_default_and_runs_with_grant():
    spec = make_spec(("save_draft",), can_write=True, tier=ToolTier.WRITE)
    b, gw, _ = mk(spec)
    o = b.execute("save_draft", {"t": "x"}, "r")
    assert o.terminal_status == RunStatus.AWAITING_CONFIRMATION and o.pending_tool == "save_draft" and gw.calls == []
    b2, gw2, _ = mk(spec, consent=StaticConsent({"save_draft"}))
    assert b2.execute("save_draft", {"t": "x"}, "r").kind == "ok" and len(gw2.calls) == 1


def test_kill_checked_before_dispatch_not_only_at_entry():
    seq = iter([True, False])                      # alive at entry, killed by the time we dispatch
    b, gw, _ = mk(session_alive=lambda: next(seq))
    o = b.execute("pub_tool", {}, "r")
    assert o.terminal_status == RunStatus.KILLED and gw.calls == []


def test_decoy_result_is_flagged_and_never_evidence():
    class UR:                                       # shape of rcap_p ToolCallResult as nested in WriteCallResult
        containment_state = "hard_blocked"; error = "decoy tool touched"
    gw = FakeGateway({"pub_tool": {"ok": False, "content": "[RCAP-P BLOCKED] decoy tool touched", "underlying_result": UR()}})
    b, _, ev = mk(gw=gw)
    o = b.execute("pub_tool", {}, "r")
    assert o.decoy is True and o.kind in ("denied", "terminal") and ev.view().ids() == []


def test_gateway_denial_exception_and_timeout_become_observations():
    gw = FakeGateway({"pub_tool": {"ok": False, "content": None, "denied_reason": "consent missing"}})
    b, _, ev = mk(gw=gw)
    assert "consent missing" in b.execute("pub_tool", {}, "r").verdict and ev.view().ids() == []

    def boom(kw): raise RuntimeError("kaboom")
    b2, _, _ = mk(gw=FakeGateway({"pub_tool": boom}))
    assert "tool_error:RuntimeError" in b2.execute("pub_tool", {}, "r").verdict

    b3, _, _ = mk(make_spec(("pub_tool",), budget=Budget(per_call_timeout_s=0.05)),
                  gw=FakeGateway({"pub_tool": lambda kw: time.sleep(0.5)}))
    assert "tool_timeout" in b3.execute("pub_tool", {}, "r").verdict


def test_success_registers_evidence_with_refs_labels_and_truncates():
    gw = FakeGateway({"read_doc": {"ok": True, "content": "A" * 9000}})
    b, _, ev = mk(gw=gw)
    o = b.execute("read_doc", {"path": "a.py"}, "r")
    assert o.evidence_id == "e1" and ev.view().has_ref("file:a.py") and "truncated" in o.observation
    assert o.labels.sensitivity.name == "INTERNAL" and o.labels.untrusted


def test_normalize_verdict_accepts_dicts_and_objects():
    class R: ok = True; content = {"a": 1}; denied_reason = None; underlying_result = None
    assert normalize_verdict(R()).ok and normalize_verdict({"ok": False, "denied_reason": "no"}).reason == "no"


@pytest.mark.parametrize("allowed,kw,msg", [
    (("nope",), {}, "unknown tool"),
    (("nuke",), dict(can_write=True, tier=ToolTier.WRITE), "SENSITIVE"),
    (("save_draft",), {}, "exceeds spec ceiling"),
    (("web_search", "rag_query"), {}, "web and RAG"),
])
def test_spec_catalog_validation(allowed, kw, msg):
    with pytest.raises(SpecError, match=msg):
        validate_spec_against_catalog(make_spec(allowed, **kw), catalog())


def test_factory_builds_brokers_without_exposing_gateway():
    f = BrokerFactory(FakeGateway(), catalog())
    assert not hasattr(f, "gateway") and hasattr(f, "make")
