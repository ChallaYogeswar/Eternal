import threading

import pytest

from eternal_core.llm.base import LLMUnavailableError
from orchestration.contracts import Labels, ModelPolicy, Provenance, Sensitivity
from orchestration.model_policy import ModelUnavailable, PolicyRouter, SpendCapExceeded, SpendLedger, cloud_eligibility
from tests.orchestration.helpers import ScriptedProvider

PUB = Labels(Sensitivity.PUBLIC, frozenset({Provenance.USER_TYPED}))
INT = Labels(Sensitivity.INTERNAL, frozenset({Provenance.USER_TYPED}))
down = lambda: [LLMUnavailableError("down")] * 3


def test_local_success_never_touches_cloud():
    local, cloud = ScriptedProvider(["hi"], "local"), ScriptedProvider(["no"], "cloud")
    r = PolicyRouter([local], [cloud]).generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 10)
    assert r.tier == "local" and not r.external_request and cloud.calls == []


def test_local_failure_with_local_only_policy_does_not_fall_through():
    cloud = ScriptedProvider(["no"], "cloud")
    with pytest.raises(ModelUnavailable) as e:
        PolicyRouter([ScriptedProvider(down(), "local")], [cloud]).generate("s", [], PUB, ModelPolicy.LOCAL_ONLY, 10)
    assert "local_only" in str(e.value) and cloud.calls == []


def test_local_failure_falls_to_cloud_when_policy_and_labels_allow():
    cloud = ScriptedProvider(["from cloud"], "cloud")
    r = PolicyRouter([ScriptedProvider(down(), "local")], [cloud]).generate("s", [], INT, ModelPolicy.CLOUD_ALLOWED, 10)
    assert r.tier == "cloud" and r.external_request and r.text == "from cloud"


@pytest.mark.parametrize("labels,why", [
    (Labels(Sensitivity.SECRET), "secret"),
    (Labels(Sensitivity.INTERNAL, pii=True), "PII"),
])
def test_cloud_blocked_by_labels_even_if_policy_allows(labels, why):
    cloud = ScriptedProvider(["x"], "cloud")
    with pytest.raises(ModelUnavailable) as e:
        PolicyRouter([ScriptedProvider(down(), "local")], [cloud]).generate("s", [], labels, ModelPolicy.CLOUD_ALLOWED, 10)
    assert why in str(e.value) and cloud.calls == []


def test_no_cloud_configured_is_reported():
    with pytest.raises(ModelUnavailable):
        PolicyRouter([ScriptedProvider(down(), "local")], [ScriptedProvider([], "c", configured=False)]).generate(
            "s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 10)


def test_cloud_failure_refunds_reservation(tmp_path):
    spend = SpendLedger(str(tmp_path / "s.db"), daily_cap_usd=1.0)
    r = PolicyRouter([ScriptedProvider(down(), "l")], [ScriptedProvider(down(), "c")], spend, price_per_1k_tokens_usd=0.1)
    with pytest.raises(ModelUnavailable):
        r.generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 1000)
    assert spend.total_today() == pytest.approx(0.0)


def test_spend_cap_blocks_cloud_call(tmp_path):
    spend = SpendLedger(str(tmp_path / "s.db"), daily_cap_usd=0.05)
    cloud = ScriptedProvider(["x"], "c")
    r = PolicyRouter([ScriptedProvider(down(), "l")], [cloud], spend, price_per_1k_tokens_usd=0.1)
    with pytest.raises(SpendCapExceeded):
        r.generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 1000)      # would cost 0.10
    assert cloud.calls == []


def test_concurrent_reservations_never_overshoot_cap(tmp_path):
    spend = SpendLedger(str(tmp_path / "s.db"), daily_cap_usd=1.0)
    ok, lock = [], threading.Lock()

    def worker():
        try:
            spend.reserve(0.1)
            with lock:
                ok.append(1)
        except SpendCapExceeded:
            pass

    ts = [threading.Thread(target=worker) for _ in range(25)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(ok) == 10 and spend.total_today() == pytest.approx(1.0)


def test_cloud_eligibility_table():
    assert cloud_eligibility(PUB, ModelPolicy.CLOUD_ALLOWED)[0]
    assert cloud_eligibility(INT, ModelPolicy.CLOUD_ALLOWED)[0]
    assert not cloud_eligibility(PUB, ModelPolicy.LOCAL_ONLY)[0]


# ---- P1: per-provider ceilings, rate limiter, pricing/settlement ------------------------------------
from eternal_core.llm.base import LLMResult
from orchestration.model_policy import CloudEntry, RateLimiter, RateLimits
from orchestration.providers.base import UsageResult


class UsageProvider(ScriptedProvider):
    def __init__(self, replies, name, tin=100, tout=50):
        super().__init__(replies, name)
        self.tin, self.tout = tin, tout

    def generate(self, system_prompt, messages):
        r = super().generate(system_prompt, messages)
        return UsageResult(text=r.text, provider=self.name, model="m", latency_seconds=0.0,
                           input_tokens=self.tin, output_tokens=self.tout)


def _router(entries, spend=None):
    return PolicyRouter([ScriptedProvider(down(), "local")], entries, spend)


def test_free_public_entry_refuses_internal_context_and_next_entry_is_used():
    free = ScriptedProvider(["f"], "free")
    paid = ScriptedProvider(["p"], "paid")
    r = _router([CloudEntry(free, Sensitivity.PUBLIC, free=True), CloudEntry(paid, Sensitivity.INTERNAL)]).generate(
        "s", [], INT, ModelPolicy.CLOUD_ALLOWED, 10)
    assert r.provider == "paid" and free.calls == []
    assert any(a.get("skipped") == "ceiling" for a in r.attempts)


def test_free_public_entry_used_for_public_context_and_costs_nothing(tmp_path):
    spend = SpendLedger(str(tmp_path / "s.db"), 1.0)
    free = UsageProvider(["f"], "free")
    r = _router([CloudEntry(free, Sensitivity.PUBLIC, free=True)], spend).generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 10)
    assert r.provider == "free" and r.cost_usd == 0 and spend.total_today() == 0
    assert (r.input_tokens, r.output_tokens) == (100, 50)


def test_only_public_provider_and_internal_context_reports_ceiling_not_silent():
    free = ScriptedProvider(["f"], "free")
    with pytest.raises(ModelUnavailable) as e:
        _router([CloudEntry(free, Sensitivity.PUBLIC, free=True)]).generate("s", [], INT, ModelPolicy.CLOUD_ALLOWED, 10)
    assert free.calls == [] and e.value.attempts[-1]["skipped"] == "ceiling"


def test_paid_spend_is_settled_to_real_usage(tmp_path):
    spend = SpendLedger(str(tmp_path / "s.db"), 10.0)
    p = UsageProvider(["x"], "paid", tin=1000, tout=500)
    e = CloudEntry(p, Sensitivity.INTERNAL, 1.0, 5.0, max_output_tokens=2000)       # USD per Mtok
    r = _router([e], spend).generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 1000)
    assert r.cost_usd == pytest.approx((1000 * 1.0 + 500 * 5.0) / 1e6)
    assert spend.total_today() == pytest.approx(r.cost_usd)


def test_paid_spend_keeps_worst_case_when_usage_not_reported(tmp_path):
    spend = SpendLedger(str(tmp_path / "s.db"), 10.0)
    e = CloudEntry(ScriptedProvider(["x"], "paid"), Sensitivity.INTERNAL, 1.0, 5.0, max_output_tokens=2000)
    r = _router([e], spend).generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 1000)
    assert r.cost_usd == pytest.approx((1000 * 1.0 + 2000 * 5.0) / 1e6) == pytest.approx(spend.total_today())


def test_spend_cap_skips_paid_but_free_entry_still_works(tmp_path):
    spend = SpendLedger(str(tmp_path / "s.db"), 0.0000001)
    paid = ScriptedProvider(["p"], "paid")
    free = ScriptedProvider(["f"], "free")
    r = _router([CloudEntry(paid, Sensitivity.INTERNAL, 10.0, 10.0, 1000),
                 CloudEntry(free, Sensitivity.PUBLIC, free=True)], spend).generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 1000)
    assert r.provider == "free" and paid.calls == []


def test_spend_cap_blocking_everything_raises_cap_exceeded(tmp_path):
    spend = SpendLedger(str(tmp_path / "s.db"), 0.0000001)
    with pytest.raises(SpendCapExceeded):
        _router([CloudEntry(ScriptedProvider(["p"], "paid"), Sensitivity.INTERNAL, 10.0, 10.0, 1000)], spend).generate(
            "s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 1000)


def test_free_entry_not_blocked_by_exhausted_spend_cap(tmp_path):
    spend = SpendLedger(str(tmp_path / "s.db"), 0.0)
    r = _router([CloudEntry(ScriptedProvider(["f"], "free"), Sensitivity.PUBLIC, free=True)], spend).generate(
        "s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 10)
    assert r.text == "f"


def test_rate_limiter_rpm_with_fake_clock():
    t = [0.0]
    rl = RateLimiter(RateLimits(rpm=2), lambda: t[0])
    assert rl.acquire(1)[0] and rl.acquire(1)[0]
    ok, why, wait = rl.acquire(1)
    assert not ok and "rpm" in why and 0 < wait <= 60
    t[0] = 61.0
    assert rl.acquire(1)[0]


def test_rate_limiter_token_windows_and_daily():
    t = [0.0]
    rl = RateLimiter(RateLimits(tpm=1000, tpd=1500), lambda: t[0])
    assert rl.acquire(800)[0]
    assert "tpm" in rl.acquire(300)[1]
    t[0] = 61.0
    assert rl.acquire(600)[0]
    t[0] = 122.0
    assert "tpd" in rl.acquire(300)[1]
    t[0] = 90000.0
    assert rl.acquire(300)[0]


def test_router_skips_rate_limited_entry_and_records_it():
    t = [0.0]
    a, b = ScriptedProvider(["a1", "a2"], "A"), ScriptedProvider(["b"], "B")
    ea = CloudEntry(a, Sensitivity.PUBLIC, free=True, limits=RateLimits(rpm=1), clock=lambda: t[0])
    eb = CloudEntry(b, Sensitivity.PUBLIC, free=True)
    r = _router([ea, eb])
    assert r.generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 10).provider == "A"
    r2 = r.generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 10)
    assert r2.provider == "B" and any(x.get("skipped") == "rate_limit" and x["wait_s"] > 0 for x in r2.attempts)


def test_all_rate_limited_is_model_unavailable_not_a_provider_call():
    p = ScriptedProvider(["x"], "A")
    e = CloudEntry(p, Sensitivity.PUBLIC, free=True, limits=RateLimits(tpm=5))
    with pytest.raises(ModelUnavailable):
        _router([e]).generate("s", [], PUB, ModelPolicy.CLOUD_ALLOWED, 100)
    assert p.calls == []


def test_free_entry_with_prices_is_rejected():
    with pytest.raises(ValueError):
        CloudEntry(ScriptedProvider([], "x"), Sensitivity.PUBLIC, 1.0, 0.0, free=True)


def test_ceiling_applies_even_though_policy_and_global_rule_allow():
    from orchestration.model_policy import cloud_eligibility
    assert cloud_eligibility(PUB, ModelPolicy.CLOUD_ALLOWED, Sensitivity.PUBLIC)[0]
    ok, why = cloud_eligibility(INT, ModelPolicy.CLOUD_ALLOWED, Sensitivity.PUBLIC)
    assert not ok and "ceiling" in why
