"""Serate con PIÙ eventi: i tavoli devono essere quelli dell'evento giusto.

Caso reale (WhatsApp, 12/9). Un cliente chiede 4 tavoli in zona F per il Perreo XL del
19 settembre, 26 persone. Il 19/9 hanno tre eventi in programma e il lookup si fermava
al PRIMO candidato con tavoli: il Carl Cox al Kozel Carroponte, che ha 10 tavoli da
€5.000 e ZERO link di acquisto. Il Perreo XL — 30 tavoli, prezzi reali e link di
checkout validi — non veniva mai interrogato. Risultato: il bot ha mandato il cliente a
scrivere a info@gatemilano.com invece di consegnargli i link di pagamento, su una
prenotazione da €1.200.

In più, i tavoli senza `checkoutUrl` venivano resi come "Prenota: None" — una stringa
che il bot rischiava di girare al cliente come se fosse un link.
"""
import httpx
import pytest

import rag.date_utils as du
from rag import context_builder as cb
from rag import event_store as es
from rag import vip_tables as vt

import datetime

_FIXED = datetime.datetime(2026, 9, 13, 15, 0, tzinfo=du._ROME)

# Il 19/9, come in produzione: off-site senza link + due serate in venue con link.
_CARROPONTE = [
    {"codice": f"T{i}", "zona": "VIP CARROPONTE", "prezzo": 5000, "coperti": 10,
     "stato": "libero", "checkoutUrl": None}
    for i in range(1, 6)
]
_PERREO = [
    {"codice": "F1", "zona": "VIP FACE", "prezzo": 600, "coperti": 10, "stato": "libero",
     "checkoutUrl": "https://booking-plugin.xceed.me/gate-milano/offer/f1"},
    {"codice": "F6", "zona": "VIP FACE", "prezzo": 300, "coperti": 8, "stato": "libero",
     "checkoutUrl": "https://booking-plugin.xceed.me/gate-milano/offer/f6"},
]


def _seed(eid, name, dstr):
    ts = int(datetime.datetime.strptime(dstr, "%Y-%m-%d")
             .replace(tzinfo=datetime.timezone.utc).timestamp())
    es.upsert_event("gate_milano", eid, f"EVENTO: {name}", {
        "type": "event", "source": "sanity", "event_name": name, "date": dstr,
        "date_ts": ts, "venue": "gate_milano", "sanity_id": eid, "ticket_url": "",
    })


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    es._store.clear()
    vt._site_cache.clear()
    monkeypatch.setattr(du, "business_now", lambda now=None: _FIXED)
    # Carl Cox indicizzato PRIMA (orario pomeridiano): è il candidato che vinceva.
    _seed("cc", "Carl Cox Invites Loco Dice", "2026-09-19")
    _seed("px", "Perreo XL", "2026-09-19")

    def _fake_client(*a, **kw):
        class _C:
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url, params=None, **kw):
                name = (params or {}).get("name", "")
                tables = _PERREO if "Perreo" in name else _CARROPONTE
                return httpx.Response(200, json={"tables": tables},
                                      request=httpx.Request("GET", "http://x"))
        return _C()

    monkeypatch.setattr(vt.httpx, "AsyncClient", _fake_client)

    async def _no_sard(*a, **k):
        return ""
    monkeypatch.setattr(cb, "get_vip_tables_sardinia", _no_sard)
    yield
    es._store.clear()
    vt._site_cache.clear()


async def test_evento_nominato_vince_sul_primo_candidato():
    # Il cliente dice "perreo": devono arrivare i tavoli del Perreo XL, con i link.
    out = await cb._vip_lookup("gate_milano", "2026-09-19", "gate-milano",
                               "4 tavoli zona F per il perreo xl del 19, siamo 26")
    assert "Perreo XL" in out
    assert "booking-plugin.xceed.me/gate-milano/offer/f1" in out
    assert "CARROPONTE" not in out


async def test_senza_nome_preferisce_i_tavoli_acquistabili():
    # Nessun evento nominato: meglio il candidato con link di acquisto che quello senza.
    out = await cb._vip_lookup("gate_milano", "2026-09-19", "gate-milano",
                               "vorrei un tavolo per sabato")
    assert "Prenota: https://booking-plugin.xceed.me" in out


async def test_mai_stampare_prenota_none():
    # Un tavolo libero senza checkoutUrl non e' acquistabile online: niente "None".
    out = await vt.get_vip_tables_via_site("Carl Cox Invites Loco Dice", "2026-09-19")
    assert "Prenota: None" not in out
    assert "None" not in out.replace("booking-plugin", "")
    assert "SENZA acquisto online" in out
    assert "info@gatemilano.com" in out


async def test_blocco_dichiara_levento():
    # Nelle serate con piu' eventi il blocco deve dire di CHI sono i tavoli, altrimenti
    # il bot attribuisce al Perreo XL i prezzi del Carroponte.
    out = await vt.get_vip_tables_via_site("Perreo XL", "2026-09-19")
    assert "evento: Perreo XL" in out


async def test_altre_serate_della_data_sono_dichiarate():
    """Il bot non deve poter dedurre dall'assenza che un evento sia senza tavoli.

    Caso reale (IG 23/9, serata del 2/10): nel contesto c'erano solo i tavoli del
    Mantikore e il bot ha risposto che per il Raw "non risultano tavoli VIP a listino
    online" — mentre il Raw aveva 4 Balcony liberi a €500 con link di acquisto.
    """
    # Nessun evento nominato: vince uno dei due, ma l'ALTRO dev'essere dichiarato.
    out = await cb._vip_lookup("gate_milano", "2026-09-19", "gate-milano",
                               "vorrei info sui tavoli della prima serata")
    assert "ALTRE SERATE DELLA STESSA DATA" in out
    assert "Carl Cox Invites Loco Dice" in out
    assert "NON dire MAI" in out


async def test_riepilogo_conta_i_tavoli_acquistabili():
    out = await cb._vip_lookup("gate_milano", "2026-09-19", "gate-milano",
                               "tavoli per il carl cox")
    # Nominato il Carl Cox: blocco completo suo (senza link), Perreo XL nel riepilogo
    # con il numero di tavoli acquistabili.
    assert "evento: Carl Cox Invites Loco Dice" in out
    assert "Perreo XL: 2 tavoli acquistabili online" in out


async def test_nome_corto_aggancia_la_serata():
    """Una serata dal nome corto ("Raw") dev'essere agganciabile.

    Il match generico scarta i token sotto le 4 lettere: qui la soglia scende a 3,
    altrimenti chi scrive "tavoli per la serata raw" riceve i tavoli dell'altro evento.
    """
    es.upsert_event("gate_milano", "raw", "EVENTO: Raw", {
        "type": "event", "source": "sanity", "event_name": "Raw",
        "date": "2026-09-19", "date_ts": int(datetime.datetime(
            2026, 9, 19, tzinfo=datetime.timezone.utc).timestamp()),
        "venue": "gate_milano", "sanity_id": "raw", "ticket_url": "",
    })
    out = await cb._vip_lookup("gate_milano", "2026-09-19", "gate-milano",
                               "tavoli per la serata raw")
    assert "evento: Raw" in out


async def test_sottostringa_non_aggancia():
    # "raw" dentro "drawn" non deve selezionare la serata Raw: match su parola intera.
    es.upsert_event("gate_milano", "raw", "EVENTO: Raw", {
        "type": "event", "source": "sanity", "event_name": "Raw",
        "date": "2026-09-19", "date_ts": int(datetime.datetime(
            2026, 9, 19, tzinfo=datetime.timezone.utc).timestamp()),
        "venue": "gate_milano", "sanity_id": "raw", "ticket_url": "",
    })
    out = await cb._vip_lookup("gate_milano", "2026-09-19", "gate-milano",
                               "we were drawn to your club, any tables?")
    assert "evento: Raw" not in out.split("ALTRE SERATE")[0]


async def test_evento_unico_nessun_riepilogo():
    # Con una sola serata in data non deve comparire il blocco "ALTRE SERATE".
    es._store.clear()
    _seed("solo", "Perreo XL", "2026-09-26")
    out = await cb._vip_lookup("gate_milano", "2026-09-26", "gate-milano", "tavoli")
    assert "ALTRE SERATE DELLA STESSA DATA" not in out
