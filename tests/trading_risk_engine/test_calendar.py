"""
Every holiday-shift case below uses a REAL date from NSE's actual 2026
circular (NSE/CMTR/71775) — traced by hand while building this module:
2026 happens to have three Tuesday holidays and two Thursday holidays,
which is enough to test both the ordinary weekly-shift case and the
monthly-expiry-lands-exactly-on-a-holiday case, on real calendar dates
rather than invented ones.
"""
import datetime as dt

import pytest

from trading_risk_engine.instruments.calendar import ExpiryResolver, NSE_TRADING_HOLIDAYS, TradingCalendar, _last_weekday_of_month
from trading_risk_engine.instruments.underlyings import get_underlying


@pytest.fixture
def resolver():
    return ExpiryResolver(TradingCalendar())


def test_weekends_are_never_trading_days():
    cal = TradingCalendar()
    assert cal.is_trading_day(dt.date(2026, 8, 15)) is False
    assert cal.is_trading_day(dt.date(2026, 8, 16)) is False


def test_ordinary_weekday_is_a_trading_day():
    cal = TradingCalendar()
    assert cal.is_trading_day(dt.date(2026, 8, 17)) is True


def test_republic_day_is_not_a_trading_day():
    cal = TradingCalendar()
    assert cal.is_trading_day(dt.date(2026, 1, 26)) is False


def test_unloaded_year_raises_rather_than_silently_assuming_open():
    cal = TradingCalendar()
    with pytest.raises(ValueError, match="no holiday calendar loaded"):
        cal.is_trading_day(dt.date(2027, 3, 1))


def test_shift_to_previous_trading_day_walks_back_over_a_holiday():
    cal = TradingCalendar()
    assert cal.shift_to_previous_trading_day(dt.date(2026, 10, 20)) == dt.date(2026, 10, 19)


def test_last_weekday_of_month_matches_the_real_november_2026_case():
    assert _last_weekday_of_month(2026, 11, 1) == dt.date(2026, 11, 24)


def test_last_weekday_of_month_matches_the_real_march_2026_case():
    assert _last_weekday_of_month(2026, 3, 3) == dt.date(2026, 3, 26)


def test_nifty_ordinary_weekly_expiry_no_holiday_involved(resolver):
    nifty = get_underlying("NIFTY")
    expiry = resolver.next_weekly_expiry(nifty, dt.date(2026, 8, 17))
    assert expiry == dt.date(2026, 8, 18)


def test_nifty_weekly_expiry_shifts_off_a_real_holiday_tuesday(resolver):
    nifty = get_underlying("NIFTY")
    expiry = resolver.next_weekly_expiry(nifty, dt.date(2026, 10, 15))
    assert expiry == dt.date(2026, 10, 19)


def test_nifty_weekly_expiry_shifts_off_diwali_balipratipada(resolver):
    nifty = get_underlying("NIFTY")
    expiry = resolver.next_weekly_expiry(nifty, dt.date(2026, 11, 5))
    assert expiry == dt.date(2026, 11, 9)


def test_asking_on_the_expiry_day_itself_returns_that_same_day_if_valid(resolver):
    nifty = get_underlying("NIFTY")
    expiry = resolver.next_weekly_expiry(nifty, dt.date(2026, 8, 18))
    assert expiry == dt.date(2026, 8, 18)


def test_banknifty_has_no_weekly_contract_at_all(resolver):
    banknifty = get_underlying("BANKNIFTY")
    with pytest.raises(ValueError, match="no weekly options"):
        resolver.next_weekly_expiry(banknifty, dt.date(2026, 8, 17))


def test_nifty_november_2026_monthly_expiry_shifts_off_guru_nanak_jayanti(resolver):
    nifty = get_underlying("NIFTY")
    expiry = resolver.next_monthly_expiry(nifty, dt.date(2026, 11, 1))
    assert expiry == dt.date(2026, 11, 23)


def test_banknifty_monthly_expiry_uses_the_same_last_tuesday_rule(resolver):
    banknifty = get_underlying("BANKNIFTY")
    expiry = resolver.next_monthly_expiry(banknifty, dt.date(2026, 11, 1))
    assert expiry == dt.date(2026, 11, 23)


def test_sensex_march_2026_monthly_expiry_shifts_off_ram_navami(resolver):
    sensex = get_underlying("SENSEX")
    expiry = resolver.next_monthly_expiry(sensex, dt.date(2026, 3, 1))
    assert expiry == dt.date(2026, 3, 25)


def test_sensex_may_2026_monthly_expiry_shifts_off_bakri_id(resolver):
    sensex = get_underlying("SENSEX")
    expiry = resolver.next_monthly_expiry(sensex, dt.date(2026, 5, 1))
    assert expiry == dt.date(2026, 5, 27)


def test_monthly_expiry_rolls_to_next_month_once_this_months_has_passed(resolver):
    nifty = get_underlying("NIFTY")
    expiry = resolver.next_monthly_expiry(nifty, dt.date(2026, 11, 25))
    assert expiry.month == 12
    assert expiry > dt.date(2026, 11, 25)


def test_monthly_expiry_rolls_across_a_year_boundary():
    stub_calendar = TradingCalendar({2026: NSE_TRADING_HOLIDAYS[2026], 2027: []})
    resolver = ExpiryResolver(stub_calendar)
    nifty = get_underlying("NIFTY")
    expiry = resolver.next_monthly_expiry(nifty, dt.date(2026, 12, 30))
    assert expiry.year == 2027
    assert expiry.month == 1


def test_futures_cycle_returns_three_ascending_monthly_expiries(resolver):
    nifty = get_underlying("NIFTY")
    cycle = resolver.futures_expiry_cycle(nifty, dt.date(2026, 8, 1), num_months=3)
    assert len(cycle) == 3
    assert cycle == sorted(cycle)
    assert len(set(m.month for m in cycle)) == 3
