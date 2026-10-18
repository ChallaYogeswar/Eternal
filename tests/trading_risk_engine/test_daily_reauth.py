import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from trading_risk_engine.broker.daily_reauth import IST, DailyReauthManager

DB = "/tmp/phase6_daily_reauth_test.db"


@pytest.fixture(autouse=True)
def _clean_db():
    if os.path.exists(DB):
        os.remove(DB)
    yield
    if os.path.exists(DB):
        os.remove(DB)


class FakeKiteClient:
    def __init__(self, api_key, login_url_value="https://kite.zerodha.com/connect/login?fake=1",
                access_token_value="fake-access-token", raise_on_exchange=None):
        self.api_key = api_key
        self._login_url_value = login_url_value
        self._access_token_value = access_token_value
        self._raise_on_exchange = raise_on_exchange
        self.generate_session_calls = []

    def login_url(self):
        return self._login_url_value

    def generate_session(self, request_token, api_secret):
        self.generate_session_calls.append((request_token, api_secret))
        if self._raise_on_exchange:
            raise self._raise_on_exchange
        return {"access_token": self._access_token_value, "user_id": "AB1234"}


def _manager(fake_client=None, **overrides):
    defaults = dict(db_path=DB, api_key="testkey", api_secret="testsecret")
    defaults.update(overrides)
    client = fake_client or FakeKiteClient(defaults["api_key"])
    return DailyReauthManager(kite_client_factory=lambda api_key: client, **defaults), client


def test_get_login_url_delegates_to_kite_client():
    manager, client = _manager()
    url = manager.get_login_url()
    assert url == client._login_url_value


def test_exchange_request_token_stores_and_returns_access_token():
    manager, client = _manager()
    token = manager.exchange_request_token("some-request-token")
    assert token == "fake-access-token"
    assert client.generate_session_calls == [("some-request-token", "testsecret")]


def test_exchange_request_token_propagates_real_failures_not_swallowed():
    manager, client = _manager(fake_client=FakeKiteClient("testkey", raise_on_exchange=ValueError("bad checksum")))
    with pytest.raises(ValueError, match="bad checksum"):
        manager.exchange_request_token("bad-token")


def test_token_status_with_no_token_ever_obtained():
    manager, _ = _manager()
    status = manager.token_status()
    assert status.has_token is False
    assert status.is_valid_now is False
    assert status.hours_remaining is None


def _obtain_token_at(manager, ist_moment: datetime, monkeypatch):
    utc_timestamp = ist_moment.astimezone(timezone.utc).timestamp()
    monkeypatch.setattr(time, "time", lambda: utc_timestamp)
    manager.exchange_request_token("rt")


def test_token_obtained_after_cutover_is_valid_all_day_and_expires_at_next_days_cutover(monkeypatch):
    manager, _ = _manager(cutover_hour_ist=7, cutover_minute_ist=30)
    day1_9am = datetime(2026, 3, 10, 9, 0, tzinfo=IST)
    _obtain_token_at(manager, day1_9am, monkeypatch)

    # Still valid late the same day
    status = manager.token_status(now_utc=datetime(2026, 3, 10, 23, 59, tzinfo=IST).astimezone(timezone.utc))
    assert status.is_valid_now is True

    # Still valid just before next day's cutover
    status = manager.token_status(now_utc=datetime(2026, 3, 11, 7, 29, tzinfo=IST).astimezone(timezone.utc))
    assert status.is_valid_now is True

    # Expires AT the cutover instant itself (exclusive boundary)
    status = manager.token_status(now_utc=datetime(2026, 3, 11, 7, 30, tzinfo=IST).astimezone(timezone.utc))
    assert status.is_valid_now is False

    # And stays invalid after
    status = manager.token_status(now_utc=datetime(2026, 3, 11, 9, 0, tzinfo=IST).astimezone(timezone.utc))
    assert status.is_valid_now is False


def test_token_obtained_before_cutover_dies_almost_immediately_same_morning(monkeypatch):
    # Matches documented real-world advice: fetching a token before the
    # daily cutover risks it being invalidated by that SAME morning's purge.
    manager, _ = _manager(cutover_hour_ist=7, cutover_minute_ist=30)
    day1_6am = datetime(2026, 3, 10, 6, 0, tzinfo=IST)
    _obtain_token_at(manager, day1_6am, monkeypatch)

    status = manager.token_status(now_utc=datetime(2026, 3, 10, 6, 30, tzinfo=IST).astimezone(timezone.utc))
    assert status.is_valid_now is True
    assert status.hours_remaining == pytest.approx(1.0, abs=0.01)

    status = manager.token_status(now_utc=datetime(2026, 3, 10, 8, 0, tzinfo=IST).astimezone(timezone.utc))
    assert status.is_valid_now is False


def test_get_valid_access_token_returns_none_when_expired(monkeypatch):
    manager, _ = _manager(cutover_hour_ist=7, cutover_minute_ist=30)
    _obtain_token_at(manager, datetime(2026, 3, 10, 9, 0, tzinfo=IST), monkeypatch)
    valid = manager.get_valid_access_token(now_utc=datetime(2026, 3, 10, 10, 0, tzinfo=IST).astimezone(timezone.utc))
    assert valid == "fake-access-token"
    expired = manager.get_valid_access_token(now_utc=datetime(2026, 3, 11, 9, 0, tzinfo=IST).astimezone(timezone.utc))
    assert expired is None


def test_only_the_latest_token_counts(monkeypatch):
    manager, client = _manager(cutover_hour_ist=7, cutover_minute_ist=30)
    _obtain_token_at(manager, datetime(2026, 3, 10, 9, 0, tzinfo=IST), monkeypatch)
    client._access_token_value = "second-token"
    _obtain_token_at(manager, datetime(2026, 3, 10, 10, 0, tzinfo=IST), monkeypatch)
    token = manager.get_valid_access_token(now_utc=datetime(2026, 3, 10, 11, 0, tzinfo=IST).astimezone(timezone.utc))
    assert token == "second-token"


def test_state_persists_across_manager_instances():
    manager, _ = _manager()
    manager.exchange_request_token("rt")
    del manager
    manager2, _ = _manager()
    status = manager2.token_status()
    assert status.has_token is True
