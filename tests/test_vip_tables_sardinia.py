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
    # pochi liberi → tavolo elencato per nome
    assert "Terrace T3 (max 10 persone) — €600 → libero" in out
    # VIP ha solo un venduto → zona esaurita
    assert "VIP: esauriti" in out
    # opzionato = hold attivo → NON elencato come libero
    assert "Terrace T1" not in out
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


class _SeqFakeClient:
    """Client fake che restituisce risposte diverse a ogni chiamata (per il retry)."""
    responses = []
    calls = 0

    def __init__(self, *a, **k):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url, params=None):
        r = _SeqFakeClient.responses[_SeqFakeClient.calls]
        _SeqFakeClient.calls += 1
        return r


@pytest.mark.asyncio
async def test_retry_recovers_after_transient_5xx(monkeypatch):
    # 1° tentativo 503 (blip), 2° tentativo 200 con tavoli → il bot li vede lo stesso,
    # niente più falso "tavoli non disponibili" per un errore momentaneo dell'endpoint.
    vip_tables._sardinia_cache.clear()
    monkeypatch.setattr(vip_tables.settings, "sardinia_site_base_url", "https://www.gatesardinia.it")
    _SeqFakeClient.calls = 0
    _SeqFakeClient.responses = [
        _FakeResponse(503, {}),
        _FakeResponse(200, {"tables": [
            {"code": "T3", "zona": "Terrace", "coperti": 10, "price": 600, "stato": "libero"},
        ]}),
    ]
    monkeypatch.setattr(vip_tables.httpx, "AsyncClient", _SeqFakeClient)
    out = await vip_tables.get_vip_tables_sardinia("evRetry")
    assert "TAVOLI VIP DISPONIBILI" in out
    assert _SeqFakeClient.calls == 2


@pytest.mark.asyncio
async def test_4xx_does_not_retry(monkeypatch):
    # un 404 (id inesistente) NON va ritentato: una sola chiamata, poi "".
    vip_tables._sardinia_cache.clear()
    monkeypatch.setattr(vip_tables.settings, "sardinia_site_base_url", "https://www.gatesardinia.it")
    _SeqFakeClient.calls = 0
    _SeqFakeClient.responses = [_FakeResponse(404, {}), _FakeResponse(200, {"tables": [{"code": "X"}]})]
    monkeypatch.setattr(vip_tables.httpx, "AsyncClient", _SeqFakeClient)
    assert await vip_tables.get_vip_tables_sardinia("evNotFound") == ""
    assert _SeqFakeClient.calls == 1


@pytest.mark.asyncio
async def test_base_url_trailing_slash_stripped(monkeypatch):
    payload = {"tables": [
        {"code": "T2", "zona": "Terrace", "coperti": 6, "price": 300, "stato": "libero"},
    ]}
    _patch(monkeypatch, _FakeResponse(200, payload), base="https://www.gatesardinia.it/")
    out = await vip_tables.get_vip_tables_sardinia("evSlash")
    assert "https://www.gatesardinia.it/tavoli?event=evSlash" in out
    assert "//tavoli" not in out.split("PRENOTA E PAGA ONLINE: ")[1]
