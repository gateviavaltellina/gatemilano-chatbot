"""Giorno del mese scritto "nudo": "il 23", "the 23rd", "sabato 23".

Caso reale (Instagram, 1/10) — il peggiore visto finora. Un cliente chiede i biglietti
per "the 23rd" (Ueberrest & In Verruf, 23 ottobre). Nessuna data veniva risolta: il
numero senza mese non era previsto in nessuna lingua. Nel contesto c'era solo la
finestra dei 14 giorni, e il bot gli ha mandato il LINK DI ACQUISTO di un'altra
serata — Schranz Movement del 3 ottobre — salvo poi dire di non avere quello del 23,
che invece esiste in calendario. Un cliente poteva comprare il biglietto per la
serata sbagliata.

Coperti qui i tre buchi emersi:
1. il giorno nudo non si risolveva ("il 23", "the 23rd", "il giorno 23");
2. "sabato 23" restituiva il sabato successivo IGNORANDO il 23, cioè una serata
   diversa da quella chiesta;
3. "il giorno 23" agganciava "giugno" (fuzzy sul mese a distanza 2) e finiva
   a giugno dell'anno dopo.
"""
import datetime

import pytest

import rag.date_utils as du
from rag.date_utils import extract_query_dates, extract_query_months

# Giovedì 1 ottobre 2026: il prossimo giorno 23 è il 23 ottobre.
_FIXED = datetime.datetime(2026, 10, 1, 15, 0, tzinfo=du._ROME)


@pytest.fixture(autouse=True)
def _clock(monkeypatch):
    real = du.business_now
    monkeypatch.setattr(du, "business_now", lambda now=None: real(_FIXED))


@pytest.mark.parametrize("text", [
    "tickets for the 23rd",
    "the 23rd",
    "on the 23rd?",
    "il 23",
    "per il 23",
    "il giorno 23",
    "sabato 23",
])
def test_giorno_nudo_risolve(text):
    assert extract_query_dates(text) == ["2026-10-23"]


def test_sabato_23_non_diventa_il_prossimo_sabato():
    # Il 3 ottobre è il sabato successivo: NON deve comparire al posto del 23.
    assert "2026-10-03" not in extract_query_dates("sabato 23")


def test_giorno_del_mese_gia_passato_va_al_mese_dopo():
    # Oggi è il 1° ottobre: "il 23" è ottobre, ma un giorno già passato scivola avanti.
    assert extract_query_dates("ci vediamo il 1") == ["2026-10-01"]


@pytest.mark.parametrize("text", [
    "sono le 23:00",
    "siamo in 23",
    "costa 23 euro",
    "ho 23 anni",
    "il tavolo costa 300€",
])
def test_numeri_che_non_sono_date(text):
    # Orari, quantità, prezzi ed età non devono mai diventare una data.
    assert extract_query_dates(text) == []


@pytest.mark.parametrize("text,expected", [
    ("il 3/5", "2027-05-03"),
    ("il 31/08", "2026-08-31"),
    ("il 04/09/2027", "2027-09-04"),
    ("il 16 novembre", "2026-11-16"),
    ("serata del 3 ottobr", "2026-10-03"),
])
def test_il_giorno_nudo_non_scavalca_le_date_complete(text, expected):
    """È il segnale più debole: vale solo se nessun pass più preciso ha deciso.

    Senza questa priorità "il 3/5" diventava il prossimo giorno 3 e "serata del 3
    ottobr" perdeva ottobre.
    """
    assert extract_query_dates(text) == [expected]


def test_trasposizione_sul_mese_riconosciuta():
    # "agsoto" per "agosto": lo scambio di due lettere è il refuso più comune.
    assert extract_query_months("agsoto") == [(2027, 8)]


def test_giorno_non_e_giugno():
    # Con la vecchia soglia "giorno" matchava "giugno": "il giorno 23" finiva a giugno.
    assert extract_query_months("il giorno 23") == []
    assert extract_query_months("che giorno aprite?") == []
