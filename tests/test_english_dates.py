"""Date scritte in inglese/spagnolo e nomi di città nei messaggi.

Caso reale (Instagram, 13/9). Un cliente in viaggio dall'India scrive: "We are trying
to be in Milan for the 16th October weekend to catch one gig". Il bot risponde che per
il 16 e 17 ottobre non ha nulla in calendario e che "anything mid-October isn't loaded
on my end yet" — mentre il 16/10 c'è Nikolina e il 17/10 il Perreo XL.

Tre guasti concatenati, tutti coperti qui:
1. Il riconoscimento "giorno + mese a parole" iterava solo i mesi ITALIANI e solo
   l'ordine giorno-mese: "16th October" e "October 16th" non producevano date.
2. La parola "Milan" agganciava i titoli degli eventi ("MILAN 8 MILE", "WEGZ LIVE IN
   MILAN"): 'milano' era fra le stopword, 'milan' no.
3. Quella data spuria (31/10) sopprimeva l'INTERO elenco del mese di ottobre, perché
   la regola salta il mese se una qualsiasi data cade in quel mese.
"""
import datetime

import pytest

import rag.date_utils as du
from rag import context_builder as cb
from rag import event_store as es
from rag.date_utils import extract_query_dates
from rag.event_store import _NAME_STOPWORDS, find_event_dates_by_name

_FIXED = datetime.datetime(2026, 9, 14, 15, 0, tzinfo=du._ROME)


@pytest.mark.parametrize("text,expected", [
    ("the 16th October weekend", "2026-10-16"),
    ("what is on 16 October?", "2026-10-16"),
    ("any events on October 16th?", "2026-10-16"),
    ("October the 17th", "2026-10-17"),
    ("16 de octubre", "2026-10-16"),
    ("il 16 ottobre", "2026-10-16"),
])
def test_date_a_parole_in_tutte_le_lingue(text, expected):
    assert expected in extract_query_dates(text)


def test_anno_esplicito_rispettato():
    assert extract_query_dates("il 15 maggio 2027") == ["2027-05-15"]


def test_may_inglese_non_e_un_mese():
    # "may" è escluso di proposito: in inglese è troppo ambiguo ("you may come").
    assert extract_query_dates("you may come with 2 friends") == []


@pytest.mark.parametrize("word", ["milan", "weekend", "italy"])
def test_citta_e_parole_di_contorno_sono_stopword(word):
    assert word in _NAME_STOPWORDS


class _Ctx:
    """Calendario minimo: un evento col nome che contiene 'Milan' + uno a metà ottobre."""

    def __init__(self):
        es._store.clear()
        self._seed("wegz", "WEGZ LIVE IN MILAN", "2026-10-31")
        self._seed("nik", "NIKOLINA & KNTRLVRLST", "2026-10-16")
        self._seed("px", "Perreo XL", "2026-10-17")

    @staticmethod
    def _seed(eid, name, dstr):
        ts = int(datetime.datetime.strptime(dstr, "%Y-%m-%d")
                 .replace(tzinfo=datetime.timezone.utc).timestamp())
        es.upsert_event("gate_milano", eid, f"EVENTO: {name}", {
            "type": "event", "source": "sanity", "event_name": name, "date": dstr,
            "date_ts": ts, "venue": "gate_milano", "sanity_id": eid, "ticket_url": "",
        })


@pytest.fixture
def calendario(monkeypatch):
    monkeypatch.setattr(du, "business_now", lambda now=None: _FIXED)
    c = _Ctx()
    async def _no_tables(*a, **k):
        return ""
    monkeypatch.setattr(cb, "get_vip_tables_via_site", _no_tables)
    monkeypatch.setattr(cb, "get_vip_tables_sardinia", _no_tables)
    yield c
    es._store.clear()


def test_milan_non_aggancia_piu_gli_eventi(calendario):
    # "in Milan" non deve risolvere l'evento "WEGZ LIVE IN MILAN".
    assert find_event_dates_by_name("gate_milano", "trying to be in Milan this month") == []


async def test_messaggio_reale_vede_meta_ottobre(calendario):
    ctx, dates = await cb.build_rag_context(
        "gate_milano",
        "We are trying to be in Milan for the 16th October weekend to catch one gig")
    # la data richiesta ora si risolve...
    assert "2026-10-16" in dates
    # ...e l'evento di quel giorno entra nel contesto
    assert "NIKOLINA" in ctx.upper()
    # niente date spurie prese dal titolo "WEGZ LIVE IN MILAN"
    assert "2026-10-31" not in dates


async def test_elenco_del_mese_non_sparisce_per_una_data_dedotta(calendario):
    # Chiedendo del mese senza un giorno esplicito, l'elenco di ottobre deve esserci
    # anche se il nome di un evento ha prodotto una data in quel mese.
    ctx, _ = await cb.build_rag_context("gate_milano", "any gigs in October in Milan?")
    assert "NIKOLINA" in ctx.upper()
    assert "ottobre" in ctx.lower()
