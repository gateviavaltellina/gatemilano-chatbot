"""Tavoli VIP Milano: redirect del sito e mappa 3D sempre disponibile.

Incidente reale (WhatsApp, 4/9). Un cliente chiede la piantina dei tavoli per il
Perreo XL del 26 settembre e il bot risponde "per questa serata non ho ancora il
link della mappa disponibile nel mio sistema", rimandando all'email. È falso: la
mappa esiste per OGNI serata e quella sera il sito aveva 30 tavoli liberi.

Due cause, entrambe coperte qui:
1. gatemilano.it risponde 308 verso www.gatemilano.it. Il client HTTP non seguiva
   i redirect, quindi riceveva 308, usciva con "" e l'INTERA pipeline tavoli di
   Milano era spenta: niente tavoli, niente prezzi reali, niente link di acquisto.
2. Anche a elenco tavoli vuoto la mappa va data lo stesso: si costruisce da
   nome+data e non dipende dai tavoli.
"""
import httpx
import pytest

from rag import vip_tables as vt


@pytest.fixture(autouse=True)
def _clear_cache():
    vt._site_cache.clear()
    yield
    vt._site_cache.clear()


def _client_factory(handler):
    """AsyncClient finto che instrada su un handler, preservando i kwargs ricevuti."""
    captured = {}

    class _FakeClient:
        def __init__(self, *a, **kw):
            captured.update(kw)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def get(self, url, params=None, **kw):
            return handler(url, params or {})

    return _FakeClient, captured


def _json_response(payload, status=200):
    return httpx.Response(status, json=payload, request=httpx.Request("GET", "http://x"))


async def test_client_segue_i_redirect(monkeypatch):
    # Senza follow_redirects il 308 di gatemilano.it -> www spegneva tutto.
    Fake, captured = _client_factory(lambda url, params: _json_response({"tables": []}))
    monkeypatch.setattr(vt.httpx, "AsyncClient", Fake)
    await vt.get_vip_tables_via_site("Perreo XL", "2026-09-26")
    assert captured.get("follow_redirects") is True


async def test_mappa_presente_anche_senza_tavoli(monkeypatch):
    # Elenco vuoto: la mappa deve esserci comunque e il bot NON deve dire che manca.
    Fake, _ = _client_factory(lambda url, params: _json_response({"tables": []}))
    monkeypatch.setattr(vt.httpx, "AsyncClient", Fake)
    out = await vt.get_vip_tables_via_site("Perreo XL", "2026-09-26")
    assert "MAPPA TAVOLI 3D (2026-09-26)" in out
    assert "mappa-vip?name=Perreo+XL&date=2026-09-26" in out
    # e deve vietare esplicitamente le due frasi sbagliate viste in chat
    assert "NON dire mai" in out
    assert "esauriti" in out


async def test_mappa_presente_con_tavoli(monkeypatch):
    tables = [{"codice": "F1", "zona": "VIP FACE", "prezzo": 600, "coperti": 10,
               "stato": "libero", "checkoutUrl": "https://booking-plugin.xceed.me/x/1"}]
    Fake, _ = _client_factory(lambda url, params: _json_response({"tables": tables}))
    monkeypatch.setattr(vt.httpx, "AsyncClient", Fake)
    out = await vt.get_vip_tables_via_site("Kobosil", "2026-09-25")
    assert "MAPPA TAVOLI 3D (2026-09-25)" in out
    assert "TAVOLI VIP DISPONIBILI" in out
    assert "https://booking-plugin.xceed.me/x/1" in out


async def test_niente_mappa_senza_nome_o_data(monkeypatch):
    # Senza nome/data la mappa non è costruibile: meglio vuoto che un link rotto.
    Fake, _ = _client_factory(lambda url, params: _json_response({"tables": []}))
    monkeypatch.setattr(vt.httpx, "AsyncClient", Fake)
    assert await vt.get_vip_tables_via_site("", "2026-09-26") == ""
    assert await vt.get_vip_tables_via_site("Perreo XL", "") == ""


async def test_http_non_200_non_inventa_la_mappa(monkeypatch):
    # Se il sito è davvero giù usciamo vuoti: il lookup prosegue sugli altri candidati
    # invece di promettere una mappa che non abbiamo potuto verificare.
    Fake, _ = _client_factory(lambda url, params: _json_response({}, status=500))
    monkeypatch.setattr(vt.httpx, "AsyncClient", Fake)
    assert await vt.get_vip_tables_via_site("Perreo XL", "2026-09-26") == ""
