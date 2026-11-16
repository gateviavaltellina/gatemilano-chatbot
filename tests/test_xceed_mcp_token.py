"""Rinnovo del token MCP Xceed (caso reale 14/11: refresh token scaduto, 400 su /token).

- se Xceed rifiuta il token: un errore chiaro con cosa fare, non "400 Bad Request";
- se lo staff rifà il consenso e aggiorna Railway, il token vecchio salvato sul volume
  non deve più avere la precedenza.
"""
import asyncio
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
            await asyncio.sleep(0)  # lascia entrare un eventuale rinnovo parallelo
            if data["refresh_token"] == good["token"]:
                if good.get("rotate"):  # Xceed ruota: il token usato non vale più
                    good["token"] = good["rotate"]
                    return _Resp(200, {"access_token": "acc", "expires_in": 3600,
                                       "refresh_token": good["rotate"]})
                return _Resp(200, {"access_token": "acc", "expires_in": 3600})
            return _Resp(400, {"error": "invalid_grant",
                               "error_description": "Invalid refresh token"})

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
    assert "invalid_grant" in str(e.value)  # il motivo di Xceed, non solo "400"


async def test_rinnovi_in_parallelo_usano_il_token_una_volta_sola(mcp):
    # Caso reale 14/11: con la rotazione, due rinnovi paralleli con lo stesso token
    # ne bruciano uno. Il lock fa rinnovare una volta sola e salva il token nuovo.
    sent, good, tmp = mcp
    good["rotate"] = "ruotato"
    assert await asyncio.gather(xm._access_token(), xm._access_token()) == ["acc", "acc"]
    assert sent == ["env-new"]
    saved = json.loads((tmp / "xceed_mcp_token.json").read_text())
    assert saved["refresh_token"] == "ruotato"
    assert not (tmp / "xceed_mcp_token.tmp").exists()


async def test_check_auth_verdetti(mcp, monkeypatch):
    sent, good, tmp = mcp
    assert await xm.check_auth() == (True, "ok")
    xm._token.update(access="", exp=0.0)
    good["token"] = "nessuno"
    ok, detail = await xm.check_auth()
    assert ok is False and "xceed_mcp_oauth.py" in detail

    async def _rete():
        raise OSError("timeout")
    monkeypatch.setattr(xm, "_access_token", _rete)
    assert (await xm.check_auth())[0] is None  # rete: non concludente


async def test_token_health_allarme_mcp_al_cambio_di_stato(monkeypatch):
    from notifications import token_health as th
    alerts = []

    async def _alert(text):
        alerts.append(text)
    verdicts = iter([(True, "ok"), (False, "scaduta"), (False, "scaduta"), (True, "ok")])

    async def _check():
        return next(verdicts)
    monkeypatch.setattr(th, "_alert", _alert)
    monkeypatch.setattr(th, "_targets", lambda: [])
    monkeypatch.setattr(xm, "configured", lambda: True)
    monkeypatch.setattr(xm, "check_auth", _check)
    th._last_status.clear()
    for _ in range(4):
        await th.check_tokens()
    th._last_status.clear()
    assert len(alerts) == 2
    assert "AUTORIZZAZIONE SCADUTA" in alerts[0] and "--railway" in alerts[0]
    assert "ripartono" in alerts[1]


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
