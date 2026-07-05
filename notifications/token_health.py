"""Health check dei token Meta (Instagram + WhatsApp).

Un token scaduto (durano ~60 giorni) fa fallire TUTTI gli invii: senza questo
check lo si scopre solo quando un cliente scrive e non riceve risposta. Qui i
token si verificano ogni ora con una GET innocua; l'allarme Discord parte SOLO
al cambio di stato (ok→scaduto e viceversa), niente spam orario.
"""
from __future__ import annotations

import logging

import httpx

from config import settings

logger = logging.getLogger(__name__)

# nome → stato ultimo check (None = mai verificato)
_last_status: dict[str, bool | None] = {}


def _targets() -> list[tuple[str, str, str]]:
    """(nome, url, token) per ogni credenziale configurata. I token IG vengono dal
    token_store (valore rinnovato più recente), non dall'env: dopo un auto-rinnovo
    l'env resta col vecchio token, lo store ha quello nuovo."""
    from instagram import token_store
    out = []
    ig_mi = token_store.get("gate_milano")
    ig_sa = token_store.get("gate_sardinia")
    if ig_mi:
        out.append(("Instagram @gatemilano", f"{settings.ig_api_url}/me", ig_mi))
    if ig_sa:
        out.append(("Instagram @gatesardinia", f"{settings.ig_api_url}/me", ig_sa))
    if settings.wa_access_token:
        out.append(("WhatsApp Cloud API",
                    f"{settings.wa_api_url}/{settings.wa_phone_number_id}", settings.wa_access_token))
    return out


async def _token_ok(url: str, token: str) -> tuple[bool | None, str]:
    """(verdetto, dettaglio). True/False = verdetto; None = check non concludente
    (problema di rete nostro, non del token): niente cambio stato né allarme.
    Il dettaglio riporta l'errore ESATTO di Meta (es. 'session invalidated
    because the user changed their password'), così la causa non va dedotta."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        if r.status_code == 200:
            return True, "ok"
        detail = r.text[:400]
        try:
            err = r.json().get("error", {})
            detail = f"[{err.get('code')}/{err.get('error_subcode', '')}] {err.get('message', '')}".strip()
        except Exception:
            pass
        if r.status_code in (400, 401, 403):
            logger.error("Token check fallito (%s): HTTP %s — %s", url, r.status_code, detail)
            return False, detail
        logger.warning("Token check non concludente (%s): HTTP %s", url, r.status_code)
        return None, f"HTTP {r.status_code}"
    except Exception as e:
        logger.warning("Token check non concludente (%s): %s", url, e)
        return None, str(e)


async def _alert(text: str) -> None:
    """Alert di sistema sul canale Discord (webhook WA come canale 'principale')."""
    url = settings.discord_webhook_url or settings.discord_ig_webhook_url
    if not url:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(url.split("?")[0], json={"content": text})
    except Exception as e:
        logger.warning("Alert token su Discord fallito: %s", e)


async def check_tokens() -> dict[str, dict]:
    """Verifica tutti i token; allarme Discord al CAMBIO di stato.
    Ritorna {nome: {"ok": bool|None, "detail": str}} col messaggio esatto di Meta."""
    results: dict[str, dict] = {}
    for name, url, token in _targets():
        ok, detail = await _token_ok(url, token)
        results[name] = {"ok": ok, "detail": detail}
        if ok is None:
            continue  # non concludente: stato invariato
        prev = _last_status.get(name)
        if prev in (True, None) and ok is False:
            await _alert(
                f"🚨 **TOKEN SCADUTO O NON VALIDO — {name}**\n"
                f"Errore Meta: {detail}\n"
                "Gli invii da questo canale stanno FALLENDO: i clienti non ricevono risposte.\n"
                "Rigenera il token su Meta Business e aggiorna la variabile su Railway."
            )
        elif prev is False and ok is True:
            await _alert(f"✅ Token {name} di nuovo valido — invii ripristinati.")
        _last_status[name] = ok
    return results


def get_token_status() -> dict[str, bool | None]:
    """Ultimo verdetto noto per token."""
    return dict(_last_status)
