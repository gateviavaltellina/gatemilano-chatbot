"""Dopo mezzanotte, prima delle 06:00, la serata di ieri sera è ANCORA quella corrente.
Caso reale (00:08 domenica 12/7): il bot diceva che il Perreo del sabato 11 era "già
finito", mentre si va avanti fino alle 03:00. La stringa data/ora del prompt e il
filtro del sync devono usare il giorno di servizio, non l'orologio a mezzanotte."""
import datetime
from zoneinfo import ZoneInfo

from rag.date_utils import format_current_datetime, business_now

_R = ZoneInfo("Europe/Rome")


def test_after_midnight_marks_current_club_night():
    t = datetime.datetime(2026, 7, 12, 0, 8, tzinfo=_R)  # notte del sabato 11
    s = format_current_datetime(t)
    assert "NOTTE CLUB IN CORSO" in s
    assert "Saturday 11 July 2026" in s
    assert "già passato" in s  # citato nella regola (NON 'già passato')


def test_before_close_still_within_rollover():
    # alle 02:00 la notte è ancora quella di ieri
    t = datetime.datetime(2026, 7, 12, 2, 0, tzinfo=_R)
    assert business_now(t).strftime("%Y-%m-%d") == "2026-07-11"
    assert "NOTTE CLUB IN CORSO" in format_current_datetime(t)


def test_daytime_no_club_night_note():
    t = datetime.datetime(2026, 7, 11, 15, 0, tzinfo=_R)
    s = format_current_datetime(t)
    assert "NOTTE CLUB IN CORSO" not in s
    assert "Saturday 11 July 2026" in s


def test_after_rollover_is_new_day():
    # dopo le 06:00 il giorno di servizio è quello nuovo, niente nota notte in corso
    t = datetime.datetime(2026, 7, 12, 8, 0, tzinfo=_R)
    assert business_now(t).strftime("%Y-%m-%d") == "2026-07-12"
    assert "NOTTE CLUB IN CORSO" not in format_current_datetime(t)
