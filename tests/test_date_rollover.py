from datetime import datetime
from zoneinfo import ZoneInfo

from rag.date_utils import business_now, extract_query_dates

_ROME = ZoneInfo("Europe/Rome")


def test_business_now_before_6am_is_previous_day():
    # 00:30 del 23 maggio → giorno di servizio = 22 maggio (notte ancora in corso)
    now = datetime(2026, 5, 23, 0, 30, tzinfo=_ROME)
    assert business_now(now).date().isoformat() == "2026-05-22"


def test_business_now_after_6am_is_same_day():
    now = datetime(2026, 5, 23, 14, 0, tzinfo=_ROME)
    assert business_now(now).date().isoformat() == "2026-05-23"


def test_stasera_after_midnight_maps_to_ongoing_night():
    now = datetime(2026, 5, 23, 0, 30, tzinfo=_ROME)
    dates = extract_query_dates("che c'e stasera?", now=now)
    assert "2026-05-22" in dates  # la serata in corso, non il 23


def test_domani_after_midnight_is_calendar_today():
    now = datetime(2026, 5, 23, 0, 30, tzinfo=_ROME)
    dates = extract_query_dates("e domani?", now=now)
    assert "2026-05-23" in dates


def test_stasera_in_daytime_maps_to_same_day():
    now = datetime(2026, 5, 23, 14, 0, tzinfo=_ROME)
    dates = extract_query_dates("che c'e stasera?", now=now)
    assert "2026-05-23" in dates
