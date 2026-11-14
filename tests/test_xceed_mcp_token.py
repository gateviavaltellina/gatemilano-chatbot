"""Rinnovo del token MCP Xceed (caso reale 14/11: refresh token scaduto, 400 su /token).

- se Xceed rifiuta il token: un errore chiaro con cosa fare, non "400 Bad Request";
- se lo staff rifà il consenso e aggiorna Railway, il token vecchio salvato sul volume
  non deve più avere la precedenza.
"""
import json

import pytest

from mail import xceed_mcp as xm
from mail import xceed_tickets as xt


class _Resp:
    def __init__(self, status, payload=None):
        self.status_code = status
        self._p = payload or {}

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def mcp(monkeypatch, tmp_path):
    sent = []
    good = {"token": "env-new"}

    class _C:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False

        async def post(self, url, data=None, **k):
            sent.append(data["refresh_token"])
            if data["refresh_token"] == good["token"]:
                return _Resp(200, {"access_token": "acc", "expires_in": 3600})
            return _Resp(400, {"error": "invalid_grant"})

    monkeypatch.setattr(xm.httpx, "AsyncClient", _C)
    monkeypatch.setattr(xm.settings, "persist_dir", str(tmp_path))
    monkeypatch.setattr(xm.settings, "xceed_mcp_client_id", "client")
    monkeypatch.setattr(xm.settings, "xceed_mcp_refresh_token", "env-new")
    xm._token.update(access="", exp=0.0, refresh="")
    yield sent, good, tmp_path
    xm._token.update(access="", exp=0.0, refresh="")


async def test_token_vecchio_sul_volume_non_vince_su_railway_aggiornato(mcp):
    sent, good, tmp = mcp
    # Salvato con le credenziali di prima (impronta diversa).
    (tmp / "xceed_mcp_token.json").write_text(json.dumps({"refresh_token": "old", "env": "x"}))
    assert await xm._access_token() == "acc"
    assert sent == ["env-new"]


async def test_se_il_salvato_e_rifiutato_si_prova_quello_di_railway(mcp):
    sent, good, tmp = mcp
    (tmp / "xceed_mcp_token.json").write_text(json.dumps({"refresh_token": "old"}))
    assert await xm._access_token() == "acc"
    assert sent == ["old", "env-new"]


async def test_tutti_rifiutati_errore_chiaro(mcp):
    sent, good, tmp = mcp
    good["token"] = "nessuno"
    with pytest.raises(xm.McpAuthError) as e:
        await xm._access_token()
    assert "xceed_mcp_oauth.py" in str(e.value) and "XCEED_MCP_REFRESH_TOKEN" in str(e.value)


async def test_ricerca_un_solo_errore_di_autorizzazione(monkeypatch):
    async def _tool(name, args):
        raise xm.McpAuthError(xm.AUTH_HELP)

    async def _events(days):
        return [{"id": 1, "uuid": "a", "name": "PERREO XL", "startingTime": 0},
                {"id": 2, "uuid": "b", "name": "ALTRO", "startingTime": 0}]

    async def _tickets(ev):
        return []

    monkeypatch.setattr(xt.settings, "xceed_api_key", "k")
    monkeypatch.setattr(xm, "configured", lambda: True)
    monkeypatch.setattr(xm, "tool_json", _tool)
    monkeypatch.setattr(xt, "recent_events", _events)
    monkeypatch.setattr(xt, "event_tickets", _tickets)
    found = await xt.find_request_tickets({"c@x.it"}, {"25307254"}, {"Annarita Cariello"})
    assert found["errors"] == [xm.AUTH_HELP]
