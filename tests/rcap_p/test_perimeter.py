from rcap_p.perimeter import PerimeterLimiter


def test_allows_calls_under_the_limit(config):
    limiter = PerimeterLimiter(config)
    limiter.set_limit_for_tool("web_search", 3)
    for _ in range(3):
        decision = limiter.check("source_a", "web_search", now=1000.0)
        assert decision.allowed

    decision = limiter.check("source_a", "web_search", now=1000.0)
    assert not decision.allowed
    assert decision.tarpitted_seconds > 0
    assert "rate limit exceeded" in decision.reason


def test_sliding_window_expires_old_calls(config):
    limiter = PerimeterLimiter(config)
    limiter.set_limit_for_tool("web_search", 2)
    limiter.check("source_a", "web_search", now=0.0)
    limiter.check("source_a", "web_search", now=1.0)
    blocked = limiter.check("source_a", "web_search", now=2.0)
    assert not blocked.allowed

    # jump past the window: old calls should no longer count
    allowed_again = limiter.check("source_a", "web_search", now=0.0 + config.perimeter_window_seconds + 1)
    assert allowed_again.allowed


def test_limits_are_independent_per_source_and_tool(config):
    limiter = PerimeterLimiter(config)
    limiter.set_limit_for_tool("web_search", 1)
    assert limiter.check("source_a", "web_search", now=0.0).allowed
    assert not limiter.check("source_a", "web_search", now=0.0).allowed
    # different source, same tool: independent budget
    assert limiter.check("source_b", "web_search", now=0.0).allowed
    # same source, different tool: independent budget
    assert limiter.check("source_a", "cinemavault_list_movies", now=0.0).allowed


def test_default_limit_applies_when_no_override_set(config):
    limiter = PerimeterLimiter(config)
    decision = limiter.check("source_a", "some_unconfigured_tool", now=0.0)
    assert decision.limit == config.perimeter_default_limit_per_window
