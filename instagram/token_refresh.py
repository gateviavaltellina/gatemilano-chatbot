"""Auto-rinnovo dei token Instagram Login (scadono ~60 giorni).

graph.instagram.com/refresh_access_token estende un token long-lived di altri
~60 giorni. Requisiti Meta: token valido e con almeno 24h di vita. Il job gira
SETTIMANALE: margine enorme (60gg), e un rinnovo saltato non causa scadenza.
Solo i token Instagram: quello WhatsApp è un System User permanente, non scade.
"""
from __future__ import annotations

import logging

import httpx

from instagram import token_store

logger = logging.getLogger(__name__)

_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"


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


async def refresh_all() -> dict[str, bool]:
    """Rinnova entrambi i token IG e li persiste. Ritorna {venue: rinnovato?}.

    Nessun alert Discord da qui: un singolo rinnovo mancato NON è un problema (il
    token resta valido settimane, e il job gira ~8 volte prima della scadenza — basta
    un successo per riestendere a 60gg). L'unica fonte di verità sugli allarmi è la
    sentinella oraria (notifications.token_health), che avvisa solo quando un token è
    DAVVERO invalido — così evitiamo falsi positivi (refresh <24h) e stato di dedup
    fragile in memoria. Su fallimento logghiamo soltanto."""
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
        else:
            logger.warning(
                "Rinnovo IG %s non riuscito (token ancora valido; la sentinella "
                "token avvisa se scade davvero)", venue,
            )
    return results
