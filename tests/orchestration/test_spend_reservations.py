import threading

import pytest

from orchestration.model_policy import SpendCapExceeded, SpendLedger


class Clock:
    def __init__(self, t=1_800_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def ledger(tmp_path, cap=1.0, clock=None):
    return SpendLedger(str(tmp_path / "s.db"), cap, clock=clock or Clock())


def test_open_counts_against_cap_and_settle_corrects(tmp_path):
    s = ledger(tmp_path)
    rid = s.open(0.40, run_id="r1", agent="auditor", provider="p")
    assert s.total_today() == pytest.approx(0.40)
    assert s.settle(rid, 0.05) is True
    assert s.total_today() == pytest.approx(0.05)
    assert s.by_run("r1")["spent_usd"] == pytest.approx(0.05)


def test_cap_blocks_open_and_leaves_no_trace(tmp_path):
    s = ledger(tmp_path, cap=0.5)
    s.open(0.4)
    with pytest.raises(SpendCapExceeded):
        s.open(0.2, run_id="rx")
    assert s.total_today() == pytest.approx(0.4)
    assert s.by_run("rx")["calls"] == 0


def test_release_refunds_and_is_idempotent(tmp_path):
    s = ledger(tmp_path)
    rid = s.open(0.3)
    assert s.release(rid) is True
    assert s.release(rid) is False
    assert s.settle(rid, 0.3) is False          # a released reservation cannot be turned back into spend
    assert s.total_today() == 0.0


def test_settle_is_idempotent_no_double_adjust(tmp_path):
    s = ledger(tmp_path)
    rid = s.open(0.3)
    assert s.settle(rid, 0.1) and not s.settle(rid, 0.1)
    assert s.total_today() == pytest.approx(0.1)


def test_reaper_releases_unsent_and_keeps_sent_as_unknown(tmp_path):
    clk = Clock()
    s = ledger(tmp_path, clock=clk)
    never_sent = s.open(0.2, run_id="a")
    sent = s.open(0.3, run_id="b")
    s.mark_sent(sent)
    clk.t += 60
    assert s.reap(stale_after_s=900) == {"released": 0, "unknown": 0}      # not stale yet
    clk.t += 1000
    assert s.reap(stale_after_s=900) == {"released": 1, "unknown": 1}
    assert s.total_today() == pytest.approx(0.3)          # unsent refunded; possibly-spent kept
    rep = s.report_today()
    assert rep["unknown_calls"] == 1 and rep["open_calls"] == 0
    assert s.by_run("b")["unknown"] == 1


def test_late_settle_from_abandoned_thread_corrects_an_unknown(tmp_path):
    clk = Clock()
    s = ledger(tmp_path, clock=clk)
    rid = s.open(0.3)
    s.mark_sent(rid)
    clk.t += 5000
    s.reap()
    assert s.settle(rid, 0.02) is True
    assert s.total_today() == pytest.approx(0.02)


def test_crash_recovery_survives_reopening_the_database(tmp_path):
    clk = Clock()
    s = ledger(tmp_path, clock=clk)
    s.open(0.25)                                           # process "crashes" here
    s2 = SpendLedger(str(tmp_path / "s.db"), 1.0, clock=clk)
    assert s2.total_today() == pytest.approx(0.25)         # the reservation is still counted: cap stays safe
    clk.t += 2000
    assert s2.reap() == {"released": 1, "unknown": 0}
    assert s2.total_today() == 0.0


def test_midnight_boundary_settles_against_the_reservations_own_day(tmp_path):
    clk = Clock(1_800_000_000.0 - (1_800_000_000.0 % 86400) + 86400 - 10)       # 10s before UTC midnight
    s = ledger(tmp_path, clock=clk)
    rid = s.open(0.4)
    clk.t += 20                                                                  # now the next UTC day
    s.settle(rid, 0.1)
    assert s.total_today() == 0.0                          # today's total is untouched by yesterday's settle
    assert s.report_today()["total_usd"] == 0.0


def test_concurrent_opens_never_overshoot_cap(tmp_path):
    s = ledger(tmp_path, cap=1.0)
    ok = []

    def go():
        try:
            s.open(0.1)
            ok.append(1)
        except SpendCapExceeded:
            pass
    ts = [threading.Thread(target=go) for _ in range(30)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 10 and s.total_today() == pytest.approx(1.0)


def test_per_agent_report(tmp_path):
    s = ledger(tmp_path)
    s.settle(s.open(0.2, agent="auditor"), 0.05)
    s.settle(s.open(0.2, agent="writer"), 0.07)
    s.release(s.open(0.2, agent="writer"))
    assert s.report_today()["per_agent_usd"] == {"auditor": 0.05, "writer": 0.07}


def test_legacy_reserve_and_adjust_still_work(tmp_path):
    s = ledger(tmp_path, cap=0.5)
    s.reserve(0.4)
    s.adjust(-0.3)
    assert s.total_today() == pytest.approx(0.1)


# ---- router integration -------------------------------------------------------------------------
from eternal_core.llm.base import LLMUnavailableError                                   # noqa: E402
from orchestration.contracts import Labels, ModelPolicy, Provenance, Sensitivity       # noqa: E402
from orchestration.model_policy import ModelUnavailable, PolicyRouter                  # noqa: E402
from tests.orchestration.helpers import ScriptedProvider                               # noqa: E402

INT = Labels(Sensitivity.INTERNAL, frozenset({Provenance.USER_TYPED}))
down = lambda: [LLMUnavailableError("down")] * 3                                        # noqa: E731


def test_router_attributes_cost_to_run_and_agent(tmp_path):
    s = SpendLedger(str(tmp_path / "s.db"), 5.0)
    r = PolicyRouter([ScriptedProvider(down(), "l")], [ScriptedProvider(["hi"], "c")], s, price_per_1k_tokens_usd=0.1)
    r.generate("s", [], INT, ModelPolicy.CLOUD_ALLOWED, 1000, run_id="run9", agent="writer")
    info = s.by_run("run9")
    assert info["calls"] == 1 and info["open"] == 0 and info["spent_usd"] > 0
    assert s.report_today()["per_agent_usd"].keys() == {"writer"}


def test_router_failure_releases_the_reservation(tmp_path):
    s = SpendLedger(str(tmp_path / "s.db"), 5.0)
    r = PolicyRouter([ScriptedProvider(down(), "l")], [ScriptedProvider(down(), "c")], s, price_per_1k_tokens_usd=0.1)
    with pytest.raises(ModelUnavailable):
        r.generate("s", [], INT, ModelPolicy.CLOUD_ALLOWED, 1000, run_id="run8", agent="writer")
    assert s.total_today() == 0.0 and s.by_run("run8")["open"] == 0


def test_hard_crash_inside_a_provider_call_leaves_a_counted_sent_reservation(tmp_path):
    class Crash(ScriptedProvider):
        def generate(self, *a, **k):
            raise KeyboardInterrupt()                # not an LLMError: nothing in the router can settle it
    clk = Clock()
    s = SpendLedger(str(tmp_path / "s.db"), 5.0, clock=clk)
    r = PolicyRouter([ScriptedProvider(down(), "l")], [Crash([], "c")], s, price_per_1k_tokens_usd=0.1)
    with pytest.raises(KeyboardInterrupt):
        r.generate("s", [], INT, ModelPolicy.CLOUD_ALLOWED, 1000, run_id="run7", agent="writer")
    assert s.total_today() > 0 and s.by_run("run7")["open"] == 1       # still counted: the cap stays safe
    clk.t += 5000
    assert s.reap() == {"released": 0, "unknown": 1}
