"""Auto-rinnovo dei token Instagram Login (scadono ~60 giorni).

graph.instagram.com/refresh_access_token estende un token long-lived di altri
~60 giorni. Requisiti Meta: token valido e con almeno 24h di vita. Il job gira
SETTIMANALE: margine enorme (60gg), e un rinnovo saltato non causa scadenza.
Solo i token Instagram: quello WhatsApp è un System User permanente, non scade.
"""
from __future__ import annotations

import logging

import httpx

from config import settings
from instagram import token_store

logger = logging.getLogger(__name__)

_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"

# stato ultimo rinnovo per venue → alert Discord solo al passaggio ok→fail
_last_ok: dict[str, bool] = {}


async def _refresh_one(token: str) -> str | None:
    """Nuovo token esteso, o None. Non solleva."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                _REFRESH_URL,
                params={"grant_type": "ig_refresh_token", "access_token": token},
            )
        if r.status_code == 200:
            return r.json().get("access_token")
        logger.warning("Rinnovo IG: HTTP %s — %s", r.status_code, r.text[:300])
    except Exception as e:
        logger.warning("Rinnovo IG: errore %s", e)
    return None


async def _alert(text: str) -> None:
    url = settings.discord_webhook_url or settings.discord_ig_webhook_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url.split("?")[0], json={"content": text})
    except Exception as e:
        logger.warning("Alert rinnovo su Discord fallito: %s", e)


async def refresh_all() -> dict[str, bool]:
    """Rinnova entrambi i token IG. Ritorna {venue: rinnovato?}."""
    results: dict[str, bool] = {}
    for venue in token_store.VENUES:
        tok = token_store.get(venue)
        if not tok:
            continue
        new = await _refresh_one(tok)
        results[venue] = bool(new)
        if new:
            token_store.set_token(venue, new)
            logger.info("Token IG %s rinnovato (+~60 giorni)", venue)
        elif _last_ok.get(venue, True):
            # primo fallimento dopo una serie di successi: avvisa senza allarmare
            await _alert(
                f"⚠️ Rinnovo automatico token Instagram **{venue}** non riuscito. "
                "Non è urgente (il token corrente è ancora valido per settimane), ma "
                "se il problema persiste andrà rigenerato a mano dal dashboard Meta."
            )
        _last_ok[venue] = bool(new)
    return results
