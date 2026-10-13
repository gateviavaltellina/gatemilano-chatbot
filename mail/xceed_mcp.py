"""Client minimale dell'MCP di Xceed (mcp.xceed-services.me), per i rimborsi.

Il consenso OAuth si dà una volta con scripts/xceed_mcp_oauth.py; qui si rinnova solo
l'access token. Se Xceed ruota il refresh token a ogni rinnovo, quello nuovo viene
salvato su disco (PERSIST_DIR): altrimenti al riavvio successivo la variabile di
Railway sarebbe già scaduta.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path

import httpx

from config import settings

logger = logging.getLogger(__name__)

_BASE = "https://mcp.xceed-services.me"
_RESOURCE = f"{_BASE}/mcp"
_token: dict = {"access": "", "exp": 0.0, "refresh": ""}
# Cloudflare davanti all'MCP blocca alcuni User-Agent "Python": nome esplicito.
_UA = {"User-Agent": "gate-milano-chatbot/1.0"}


def configured() -> bool:
    return bool(settings.xceed_mcp_client_id and (_saved_refresh() or settings.xceed_mcp_refresh_token))


def _token_file() -> Path | None:
    d = (settings.persist_dir or "").strip()
    return Path(d) / "xceed_mcp_token.json" if d else None


def _saved_refresh() -> str:
    f = _token_file()
    try:
        if f and f.exists():
            return json.loads(f.read_text()).get("refresh_token", "")
    except Exception:
        logger.debug("Token MCP salvato non leggibile", exc_info=True)
    return ""


def _save_refresh(token: str) -> None:
    f = _token_file()
    if not f:
        return
    try:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"refresh_token": token}))
    except Exception:
        logger.warning("Refresh token MCP non salvato su disco", exc_info=True)


async def _access_token() -> str:
    if _token["access"] and time.time() < _token["exp"]:
        return _token["access"]
    refresh = _token["refresh"] or _saved_refresh() or settings.xceed_mcp_refresh_token
    async with httpx.AsyncClient(timeout=20, headers=_UA) as client:
        r = await client.post(f"{_BASE}/token", data={
            "grant_type": "refresh_token", "refresh_token": refresh,
            "client_id": settings.xceed_mcp_client_id, "resource": _RESOURCE,
        })
    r.raise_for_status()
    data = r.json()
    _token["access"] = data["access_token"]
    _token["exp"] = time.time() + int(data.get("expires_in", 3600)) - 120
    new_refresh = data.get("refresh_token")
    if new_refresh and new_refresh != refresh:
        _token["refresh"] = new_refresh
        _save_refresh(new_refresh)
    return _token["access"]


def _parse(resp: httpx.Response) -> dict:
    text = resp.text.strip()
    if text.startswith("{"):
        return json.loads(text)
    for line in text.splitlines():
        if line.startswith("data:") and line[5:].strip().startswith("{"):
            return json.loads(line[5:].strip())
    raise ValueError(f"Risposta MCP non riconosciuta: {text[:200]}")


async def _rpc(method: str, params: dict | None = None) -> dict:
    """Una chiamata JSON-RPC su una sessione MCP nuova (poche chiamate, niente stato)."""
    headers = {"Authorization": f"Bearer {await _access_token()}",
               "Accept": "application/json, text/event-stream",
               "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60, headers=_UA) as client:
        init = await client.post(_RESOURCE, headers=headers, json={
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                       "clientInfo": {"name": "gate-milano-chatbot", "version": "1"}}})
        init.raise_for_status()
        if init.headers.get("Mcp-Session-Id"):
            headers["Mcp-Session-Id"] = init.headers["Mcp-Session-Id"]
        await client.post(_RESOURCE, headers=headers,
                          json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        r = await client.post(_RESOURCE, headers=headers, json={
            "jsonrpc": "2.0", "id": 2, "method": method, "params": params or {}})
        r.raise_for_status()
        out = _parse(r)
    if "error" in out:
        raise RuntimeError(f"MCP {method}: {out['error']}")
    return out.get("result", {})


async def list_tools() -> list[dict]:
    return (await _rpc("tools/list")).get("tools", [])


async def call_tool(name: str, arguments: dict) -> dict:
    return await _rpc("tools/call", {"name": name, "arguments": arguments})


_REFUND_MESSAGE = {
    "it": "Rimborso per ingresso non consentito all'ingresso — Gate Milano",
    "en": "Refund for denied entry at the door — Gate Milano",
}


async def refund_booking(booking_id: str | int, lang: str = "it") -> dict:
    """Rimborso TOTALE di una prenotazione (strumento `booking_refund` dell'MCP).

    Vuole l'ID numerico "legacy" della prenotazione: è il `bookingId` che la Partner
    API restituisce per ogni biglietto. Policy: importo pieno, commissioni incluse.
    È un'operazione irreversibile: la si chiama solo dopo un `!rimborsa` dello staff
    (o con REFUND_AUTO_EXECUTE acceso).
    """
    result = await call_tool(settings.xceed_mcp_refund_tool, {
        "legacyId": int(booking_id),
        "isFullRefund": True,
        "message": _REFUND_MESSAGE["en" if lang not in ("it", "", None) else "it"],
    })
    if result.get("isError"):
        text = " ".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
        raise RuntimeError(f"Xceed ha rifiutato il rimborso di {booking_id}: {text[:200]}")
    return result


async def tool_json(name: str, arguments: dict):
    """Risultato di uno strumento come JSON (o testo, se non è JSON)."""
    result = await call_tool(name, arguments)
    texts = [c.get("text", "") for c in result.get("content", [])
             if isinstance(c, dict) and c.get("type") == "text"]
    if result.get("isError"):
        raise RuntimeError(f"{name}: {' '.join(texts)[:200]}")
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    joined = "\n".join(texts).strip()
    try:
        return json.loads(joined)
    except ValueError:
        return joined
