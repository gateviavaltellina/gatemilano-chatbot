"""Unit test (offline) della disponibilità tavoli VIP Sardegna via endpoint sito."""
import pytest

from rag import vip_tables


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, raise_exc=None):
        self.status_code = status_code
        self._payload = payload or {}
        self._raise_exc = raise_exc

    def json(self):
        if self._raise_exc:
            raise self._raise_exc
        return self._payload


class _FakeClient:
    """Sostituisce httpx.AsyncClient: cattura l'URL e ritorna una risposta fissa."""
    last_url = None
    last_params = None

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        _FakeClient.last_url = url
        _FakeClient.last_params = params
        return self._response


def _patch(monkeypatch, response, base="https://www.gatesardinia.it"):
    vip_tables._sardinia_cache.clear()
    monkeypatch.setattr(vip_tables.settings, "sardinia_site_base_url", base)

    client = _FakeClient
    client._response = response
    monkeypatch.setattr(vip_tables.httpx, "AsyncClient", client)
    return client


@pytest.mark.asyncio
async def test_empty_sanity_id_no_call(monkeypatch):
    _FakeClient.last_url = None
    _patch(monkeypatch, _FakeResponse(200, {"tables": []}))
    assert await vip_tables.get_vip_tables_sardinia("") == ""
    # nessuna chiamata di rete
    assert _FakeClient.last_url is None


@pytest.mark.asyncio
async def test_available_and_unavailable(monkeypatch):
    payload = {
        "event": "ev123",
        "tables": [
            {"code": "T3", "zona": "Terrace", "coperti": 10, "price": 600, "stato": "libero"},
            {"code": "V12", "zona": "VIP", "coperti": 6, "price": 300, "stato": "venduto"},
            {"code": "T1", "zona": "Terrace", "coperti": 10, "price": 600, "stato": "opzionato"},
        ],
    }
    _patch(monkeypatch, _FakeResponse(200, payload))
    out = await vip_tables.get_vip_tables_sardinia("ev123")

    assert "TAVOLI VIP DISPONIBILI" in out
    assert "Terrace T3 — max 10 persone: minimo €600 → libero" in out
    assert "VIP V12 — max 6 persone: minimo €300 — NON DISPONIBILE" in out
    # opzionato = hold attivo → non disponibile
    assert "Terrace T1 — max 10 persone: minimo €600 — NON DISPONIBILE" in out
    assert "PRENOTA E PAGA ONLINE: https://www.gatesardinia.it/tavoli?event=ev123" in out
    assert _FakeClient.last_params == {"event": "ev123"}


@pytest.mark.asyncio
async def test_all_sold_out_still_links(monkeypatch):
    payload = {"tables": [
        {"code": "V1", "zona": "VIP", "coperti": 10, "price": 600, "stato": "venduto"},
    ]}
    _patch(monkeypatch, _FakeResponse(200, payload))
    out = await vip_tables.get_vip_tables_sardinia("evSold")
    assert "tutti esauriti" in out
    assert "PRENOTA E PAGA ONLINE: https://www.gatesardinia.it/tavoli?event=evSold" in out


@pytest.mark.asyncio
async def test_no_tables_returns_empty(monkeypatch):
    _patch(monkeypatch, _FakeResponse(200, {"tables": []}))
    assert await vip_tables.get_vip_tables_sardinia("evX") == ""


@pytest.mark.asyncio
async def test_http_error_returns_empty(monkeypatch):
    _patch(monkeypatch, _FakeResponse(500, {"tables": [{"code": "T1"}]}))
    assert await vip_tables.get_vip_tables_sardinia("evErr") == ""


@pytest.mark.asyncio
async def test_exception_returns_empty(monkeypatch):
    _patch(monkeypatch, _FakeResponse(200, raise_exc=ValueError("boom")))
    assert await vip_tables.get_vip_tables_sardinia("evBoom") == ""


@pytest.mark.asyncio
async def test_base_url_trailing_slash_stripped(monkeypatch):
    payload = {"tables": [
        {"code": "T2", "zona": "Terrace", "coperti": 6, "price": 300, "stato": "libero"},
    ]}
    _patch(monkeypatch, _FakeResponse(200, payload), base="https://www.gatesardinia.it/")
    out = await vip_tables.get_vip_tables_sardinia("evSlash")
    assert "https://www.gatesardinia.it/tavoli?event=evSlash" in out
    assert "//tavoli" not in out.split("PRENOTA E PAGA ONLINE: ")[1]
