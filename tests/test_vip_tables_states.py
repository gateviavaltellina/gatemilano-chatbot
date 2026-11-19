"""Stati dei tavoli VIP Milano dall'endpoint del sito.

Caso reale (Nikolina & KNTRLVRLST 16/10): tutti i tavoli con stato "chiuso"
(vendita online non ancora aperta) → il blocco diceva "tutti esauriti" e il bot
ripiegava sulla tabella statica, quotando €300 un tavolo B4 che quella sera
costa €500 e girando il link evento generico invece di gestire il caso.
"""
import pytest

import rag.vip_tables as vt


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._p = payload

    def json(self):
        return self._p


class _C:
    _payload = None

    def __init__(self, *a, **k): pass
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def get(self, url, params=None): return _Resp(_C._payload)


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    vt._site_cache.clear()
    monkeypatch.setattr(vt.httpx, "AsyncClient", _C)


def _t(cod, stato, prezzo=500):
    return {"codice": cod, "zona": "VIP BALCONY", "prezzo": prezzo, "coperti": 10,
            "stato": stato, "checkoutUrl": f"https://booking-plugin.xceed.me/x/{cod}"}


async def test_closed_tables_show_real_prices_not_soldout():
    _C._payload = {"tables": [_t("B1", "chiuso"), _t("B4", "chiuso")]}
    out = await vt.get_vip_tables_via_site("NIKOLINA & KNTRLVRLST", "2026-10-16")
    assert "NON ANCORA APERTA" in out
    assert "esauriti" not in out.split("\n")[1].lower() or "NON dire mai" in out
    assert "€500" in out                       # prezzo REALE della serata visibile
    assert "tutti esauriti" not in out
    assert "info@gatemilano.com" in out        # via per bloccare in anticipo
    assert "MAPPA TAVOLI 3D" in out


async def test_free_tables_have_direct_checkout_links():
    _C._payload = {"tables": [_t("B2", "libero"), _t("B3", "venduto")]}
    out = await vt.get_vip_tables_via_site("EVENTO", "2026-10-16")
    assert "TAVOLI VIP DISPONIBILI — evento:" in out
    assert "Prenota: https://booking-plugin.xceed.me/x/B2" in out
    assert "B3" in out and "NON DISPONIBILE" in out


async def test_all_sold_is_soldout():
    _C._payload = {"tables": [_t("B1", "venduto"), _t("B2", "occupato")]}
    out = await vt.get_vip_tables_via_site("EVENTO", "2026-10-16")
    assert "tutti esauriti" in out


# --- prezzi della serata (Halloween 31/10: tutto più caro e tavoli da 10) -----------------

def _tz(zona, cod, prezzo, cop, stato="libero"):
    return {"codice": cod, "zona": zona, "prezzo": prezzo, "coperti": cop, "stato": stato,
            "checkoutUrl": f"https://booking-plugin.xceed.me/x/{cod}"}


async def test_serata_speciale_prezzi_e_niente_listino():
    _C._payload = {"tables": [_tz("VIP FACE", "F1", 900, 10), _tz("VIP FACE", "F10", 600, 10),
                              _tz("VIP FACE", "F9", 600, 10), _tz("VIP BALCONY", "B3", 600, 10),
                              _tz("VIP FACE", "F6", 600, 10, "venduto")]}
    out = await vt.get_vip_tables_via_site("PERREO XL", "2026-10-31")
    assert "PREZZI DI QUESTA SERATA" in out
    assert "VIP FACE €600, fino a 10 persone, persona extra: supplemento NON a listino — F9, F10" in out
    assert "Minimo più basso stasera: €600" in out
    assert "PREZZI SPECIALI" in out


async def test_serata_standard_supplementi_a_listino():
    _C._payload = {"tables": [_tz("VIP FACE", "F1", 600, 10), _tz("VIP FACE", "F6", 300, 8),
                              _tz("VIP BALCONY", "B1", 300, 8)]}
    out = await vt.get_vip_tables_via_site("PERREO XL", "2026-11-07")
    assert "VIP FACE €300, fino a 8 persone, persona extra €35 (si paga all'ingresso) — F6" in out
    assert "VIP FACE €600, fino a 10 persone, persona extra €50 (si paga all'ingresso) — F1" in out
    assert "Minimo più basso stasera: €300" in out
    assert "PREZZI SPECIALI" not in out


async def test_checkout_sul_sito_manda_la_mappa_non_info():
    """Adrián Mills all'Alcatraz (27/2/2027): tavoli pagati sul sito, niente link per tavolo."""
    _C._payload = {"checkout": "revolut", "venue": "Alcatraz Milano",
                   "tables": [{"codice": "B1", "zona": "TAVOLI", "prezzo": 400, "coperti": 10,
                               "stato": "libero", "checkoutUrl": None}]}
    out = await vt.get_vip_tables_via_site("ADRIÁN MILLS - ALL NIGHT LONG", "2027-02-27")
    assert "sede: Alcatraz Milano" in out
    assert "PRENOTA E PAGA ONLINE sulla mappa: https://gatemilano.it/mappa-vip?" in out
    assert "TAVOLI €400, fino a 10 persone, persona extra €40 (si paga all'ingresso)" in out
    row = [l for l in out.splitlines() if l.startswith("- TAVOLI B1")][0]
    assert "prenotabile sulla mappa" in row and "info@" not in row


async def test_link_per_prenotare_subito_uno_per_fascia():
    """Caso reale IG 19/11: con 30 righe di link il bot mandava a info@. In testa al blocco
    c'è un link pronto per ogni fascia (zona/prezzo/coperti)."""
    _C._payload = {"tables": [_tz("VIP FACE", "F6", 300, 8), _tz("VIP FACE", "F7", 300, 8),
                              _tz("VIP FACE", "F1", 600, 10), _tz("VIP BALCONY", "B1", 300, 8, "venduto")]}
    out = await vt.get_vip_tables_via_site("PERREO XL", "2026-11-21")
    quick = out.split("LINK PER PRENOTARE SUBITO")[1].split("\n- ")[0]
    assert "VIP FACE €300 fino a 8 persone (F6): https://booking-plugin.xceed.me/x/F6" in quick
    assert "VIP FACE €600 fino a 10 persone (F1)" in quick
    assert "F7" not in quick and "B1" not in quick
