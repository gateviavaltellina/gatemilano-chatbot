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


# --- Date NUMERICHE (caso reale 31/07/26: non parsata → cross-venue mai scattato) ---

def test_numeric_dates_parsed(monkeypatch):
    import datetime
    import rag.date_utils as du
    monkeypatch.setattr(du, "business_now",
                        lambda now=None: datetime.datetime(2026, 7, 25, 15, 0, tzinfo=du._ROME))
    assert du.extract_query_dates("per la serata del 31/07/26 posso entrare?") == ["2026-07-31"]
    assert du.extract_query_dates("il 31/07 siete aperti?") == ["2026-07-31"]
    assert du.extract_query_dates("serata del 31-07-2026") == ["2026-07-31"]
    assert du.extract_query_dates("che c'è il 31.07.26?") == ["2026-07-31"]
    # senza anno e passata DA POCO (≤90gg) → resta quest'anno, al passato
    # ("che serata c'era?" si riferisce alla serata passata, non al 2027!)
    assert du.extract_query_dates("il 3/5 che serata c'era?") == ["2026-05-03"]
    # senza anno e passata da MESI (oltre la finestra) → anno prossimo
    assert du.extract_query_dates("per il 3/1 si può prenotare?") == ["2027-01-03"]


def test_numeric_dates_no_false_positives(monkeypatch):
    import datetime
    import rag.date_utils as du
    monkeypatch.setattr(du, "business_now",
                        lambda now=None: datetime.datetime(2026, 7, 25, 15, 0, tzinfo=du._ROME))
    assert du.extract_query_dates("un drink costa 5.10 giusto?") == []      # prezzo col punto
    assert du.extract_query_dates("chiudete alle 22:00?") == []              # orario coi due punti
    assert du.extract_query_dates("ho 16/17 anni") == []                     # mese 17 invalido


def test_explicit_only_skips_relative_terms(monkeypatch):
    import datetime
    import rag.date_utils as du
    monkeypatch.setattr(du, "business_now",
                        lambda now=None: datetime.datetime(2026, 7, 25, 15, 0, tzinfo=du._ROME))
    # relativi ignorati in explicit_only, esplicite mantenute (numeriche e a parole)
    assert du.extract_query_dates("stasera o sabato?", explicit_only=True) == []
    assert du.extract_query_dates("la serata del 31/07/26", explicit_only=True) == ["2026-07-31"]
    assert du.extract_query_dates("il 15 agosto che c'è?", explicit_only=True) == ["2026-08-15"]
