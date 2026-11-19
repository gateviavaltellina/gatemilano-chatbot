"""Feste nominate a parole → data della serata.

Caso reale IG 19/11: "capodanno" non diventava una data; il bot vedeva solo i prossimi
14 giorni e diceva che a Capodanno non c'era niente, con il Perreo XL New Year's Eve
del 31/12 già in vendita.
"""
from datetime import datetime

import pytest

from rag import date_utils as du

NOW = datetime(2026, 11, 19, 17, 0, tzinfo=du._ROME)


@pytest.mark.parametrize("text,day", [
    ("cosa fate a capodanno?", "2026-12-31"),
    ("what's on for New Year's Eve?", "2026-12-31"),
    ("NYE party?", "2026-12-31"),
    ("serata di san silvestro", "2026-12-31"),
    ("aperti a natale?", "2026-12-25"),
    ("rimborso per halloween", "2026-10-31"),   # appena passata: resta quella
])
def test_feste(text, day):
    assert day in du.extract_query_dates(text, NOW)
    assert day in du.extract_query_dates(text, NOW, explicit_only=True)


def test_niente_falsi_positivi():
    assert du.extract_query_dates("Grazie ma ho visto evento in vendita", NOW) == []
    assert du.extract_query_dates("capodannoxx", NOW) == []
