"""Typo del cliente su giorni e date — pass fuzzy del parser.

Dal collaudo staff (29/8): "il 19 setembre" perdeva il giorno (restava solo il
match a livello mese) e "sbato"/"vnerdì" non agganciavano il giorno della
settimana. I typo sugli artisti ("kobosll") e sui mesi ("settembr") erano già
coperti dal fuzzy esistente.
"""
import datetime
from zoneinfo import ZoneInfo

import rag.date_utils as du

NOW = datetime.datetime(2026, 9, 1, 12, 0, tzinfo=ZoneInfo("Europe/Rome"))


def _q(text):
    return du.extract_query_dates(text, now=NOW)


def test_month_typo_keeps_the_day():
    assert _q("il 19 setembre che c'è?") == ["2026-09-19"]
    assert _q("serata del 3 ottobr") == ["2026-10-03"]
    assert _q("eventi il 24 dicembr?") == ["2026-12-24"]


def test_weekday_typos():
    # martedì 1/9: sabato prossimo = 5/9, venerdì = 4/9
    assert "2026-09-05" in _q("sbato che si fa?")
    assert "2026-09-04" in _q("vnerdì sera c'è qualcosa?")
    # e col typo funziona anche "scorso" (indietro)
    assert "2026-08-29" in _q("le foto di sbato scorso")


def test_no_false_positives():
    # parole comuni non devono diventare giorni/date
    assert _q("avete un menu completo?") == []      # "completo" ≠ mese
    assert _q("mi date lo stato dell'ordine 19?") == []
    assert _q("sono state 19 serate bellissime") == []


def test_exact_terms_still_work():
    assert _q("il 19 settembre") == ["2026-09-19"]
    assert "2026-09-05" in _q("sabato")
