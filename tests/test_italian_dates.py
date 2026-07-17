"""Giorno della settimana nelle date degli eventi.

Bug reale: il bot diceva "Papa V è sabato 19 luglio" mentre il 19/07/2026 è
DOMENICA. Causa: la riga "Data:" veniva formattata senza il giorno della
settimana (strftime %-d %B), quindi il modello lo indovinava — sbagliando.
Ora il giorno lo calcola Python e finisce dentro la riga "Data:".
"""
import datetime

from rag.date_utils import format_italian_date, italian_weekday, italian_month_year
from sync.sanity_sync import _build_document
from sync.xceed_sync import _build_event_document


def test_italian_weekday_known_dates():
    assert italian_weekday(datetime.date(2026, 7, 17)) == "venerdì"
    assert italian_weekday(datetime.date(2026, 7, 18)) == "sabato"
    assert italian_weekday(datetime.date(2026, 7, 19)) == "domenica"
    assert italian_weekday(datetime.date(2026, 7, 20)) == "lunedì"


def test_format_italian_date():
    assert format_italian_date(datetime.date(2026, 7, 19)) == "domenica 19 luglio 2026"
    assert format_italian_date(datetime.date(2026, 7, 19), with_year=False) == "domenica 19 luglio"


def test_italian_month_year():
    assert italian_month_year(2026, 7) == "luglio 2026"
    assert italian_month_year(2026, 12) == "dicembre 2026"


def test_locale_independent():
    # Non deve dipendere da strftime("%A"/"%B"), che senza locale it_IT darebbe
    # inglese ("Sunday"/"July").
    d = datetime.date(2026, 7, 19)
    s = format_italian_date(d)
    assert "Sunday" not in s and "July" not in s
    assert "domenica" in s and "luglio" in s


def test_sanity_data_line_has_weekday():
    # Papa V: 19 luglio 2026 = domenica (NON sabato)
    doc, _ = _build_document(
        {"_id": "papav", "title": "Papa V", "date": "2026-07-19T22:00:00+02:00"},
        "Gate Sardinia")
    data_line = next(l for l in doc.split("\n") if l.startswith("Data:"))
    assert "domenica 19 luglio 2026" in data_line
    assert "sabato" not in data_line


def test_sanity_data_line_has_weekday_dateonly():
    doc, _ = _build_document({"_id": "x", "title": "X", "date": "2026-07-20"}, "Gate Sardinia")
    data_line = next(l for l in doc.split("\n") if l.startswith("Data:"))
    assert "lunedì 20 luglio 2026" in data_line


def test_xceed_data_line_has_weekday():
    # 18 luglio 2026 23:00 Rome = sabato. Timestamp = 21:00 UTC.
    ts = int(datetime.datetime(2026, 7, 18, 21, 0, tzinfo=datetime.timezone.utc).timestamp())
    doc, _ = _build_event_document({"name": "Test", "id": "t1", "startingTime": ts}, "Gate Milano", {})
    data_line = next(l for l in doc.split("\n") if l.startswith("Data:"))
    assert "sabato 18 luglio 2026" in data_line
