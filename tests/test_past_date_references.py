"""Riferimenti a serate PASSATE — parser date.

Caso reale (audit 29/8): "la serata del 4 settembre" chiesta il 10/9 veniva
risolta al 4 settembre 2027 (il parser spostava sempre avanti le date senza
anno) e "venerdì scorso" diventava venerdì PROSSIMO → la nota "date passate"
non scattava mai e il bot rispondeva "nessun evento in programma per il 2027".
Ora: data senza anno passata da poco (≤90 giorni) = quest'anno (passata);
"scorso/passato/last" = occorrenza precedente; "ieri" supportato.
"""
import datetime
from zoneinfo import ZoneInfo

import rag.date_utils as du

ROME = ZoneInfo("Europe/Rome")
NOW = datetime.datetime(2026, 9, 10, 15, 0, tzinfo=ROME)  # giovedì 10/9


def _q(text):
    return du.extract_query_dates(text, now=NOW)


def test_recent_past_day_month_stays_this_year():
    assert _q("cercavo le foto della serata del 4 settembre") == ["2026-09-04"]
    assert _q("la serata del 22 agosto com'era?") == ["2026-08-22"]
    assert _q("il 31/08 chi suonava?") == ["2026-08-31"]


def test_far_past_day_month_rolls_to_next_year():
    # oltre la finestra dei 90 giorni: torna l'interpretazione "anno prossimo"
    assert _q("prenoto per il 15 maggio") == ["2027-05-15"]


def test_explicit_year_never_touched():
    assert _q("la serata del 4 settembre 2026") == ["2026-09-04"]
    assert _q("il 04/09/2027") == ["2027-09-04"]


def test_weekday_scorso_goes_backward():
    # giovedì 10/9: "venerdì scorso" = 4/9 (non l'11/9!)
    assert "2026-09-04" in _q("ho perso la giacca venerdì scorso")
    assert "2026-09-11" not in _q("ho perso la giacca venerdì scorso")
    # "sabato scorso" = 5/9
    assert "2026-09-05" in _q("le foto di sabato scorso")
    # "weekend scorso" = sab 5 + dom 6
    ws = _q("com'era il weekend scorso?")
    assert "2026-09-05" in ws and "2026-09-06" in ws


def test_weekday_without_scorso_still_forward():
    assert "2026-09-11" in _q("venerdì che si fa?")
    assert "2026-09-12" in _q("sabato prossimo")


def test_ieri():
    assert "2026-09-09" in _q("ieri sera ho dimenticato il telefono da voi")
    # confine di parola: "pensieri" non è "ieri"
    assert _q("ho dei pensieri") == []


def test_future_day_month_unchanged():
    assert _q("il 19 settembre c'è carl cox?") == ["2026-09-19"]
