import time

from rcap_p.rotate import RotateRing


def test_grant_then_check_allows(config, ledger):
    rotate = RotateRing(config, ledger)
    token = rotate.grant("sess-1", "cinemavault_list_movies")
    decision = rotate.check("sess-1", "cinemavault_list_movies", token)
    assert decision.allowed


def test_no_token_is_denied(config, ledger):
    rotate = RotateRing(config, ledger)
    decision = rotate.check("sess-1", "cinemavault_list_movies", None)
    assert not decision.allowed
    assert "no permission token" in decision.reason


def test_token_scoped_to_wrong_tool_is_denied(config, ledger):
    rotate = RotateRing(config, ledger)
    token = rotate.grant("sess-1", "cinemavault_list_movies")
    decision = rotate.check("sess-1", "web_search", token)
    assert not decision.allowed
    assert "scoped to" in decision.reason


def test_token_from_a_different_session_is_denied(config, ledger):
    rotate = RotateRing(config, ledger)
    token = rotate.grant("sess-1", "cinemavault_list_movies")
    decision = rotate.check("sess-2", "cinemavault_list_movies", token)
    assert not decision.allowed
    assert "different session" in decision.reason


def test_expired_token_is_denied(config, ledger):
    config.rotate_token_ttl_seconds = 0.05
    rotate = RotateRing(config, ledger)
    token = rotate.grant("sess-1", "cinemavault_list_movies")
    time.sleep(0.1)
    decision = rotate.check("sess-1", "cinemavault_list_movies", token)
    assert not decision.allowed
    assert "expired" in decision.reason


def test_tampered_token_is_rejected_not_crashed(config, ledger):
    rotate = RotateRing(config, ledger)
    token = rotate.grant("sess-1", "cinemavault_list_movies")
    tampered = token[:-4] + "AAAA"
    decision = rotate.check("sess-1", "cinemavault_list_movies", tampered)
    assert not decision.allowed


def test_revoke_session_invalidates_all_its_tokens(config, ledger):
    rotate = RotateRing(config, ledger)
    t1 = rotate.grant("sess-1", "cinemavault_list_movies")
    t2 = rotate.grant("sess-1", "web_search")
    count = rotate.revoke_session("sess-1")
    assert count == 2
    assert not rotate.check("sess-1", "cinemavault_list_movies", t1).allowed
    assert not rotate.check("sess-1", "web_search", t2).allowed


def test_revoked_token_cannot_be_reused_even_before_ttl(config, ledger):
    rotate = RotateRing(config, ledger)
    token = rotate.grant("sess-1", "cinemavault_list_movies")
    rotate.revoke_session("sess-1")
    decision = rotate.check("sess-1", "cinemavault_list_movies", token)
    assert not decision.allowed
    assert "revoked" in decision.reason
