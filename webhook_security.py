"""Verifica della firma dei webhook Meta (WhatsApp + Instagram).

Meta firma ogni POST con HMAC-SHA256 del body grezzo usando l'app secret,
e invia il risultato nell'header `X-Hub-Signature-256: sha256=<hex>`.
Senza questa verifica chiunque conosca l'URL pubblico può iniettare messaggi
falsi (costi API Claude + invii WhatsApp/IG dal vostro account).
"""
import hashlib
import hmac
import logging

from fastapi import Request, HTTPException

from config import settings

logger = logging.getLogger(__name__)
_warned_no_secret = False

# Contatore dei webhook RESPINTI per firma mancante/non valida: se META_APP_SECRET
# su Railway non corrisponde all'app secret dell'app Meta che chiama il webhook
# (caso reale: secret cambiato configurando il concierge), il bot smette di
# ricevere TUTTO in silenzio — qui il guasto diventa visibile in !stato.
_rejected_count = 0
_last_rejected_at = ""

# Allarme Discord AUTOMATICO al primo respinto (poi al massimo uno l'ora): il
# caso reale del 20/8 è rimasto invisibile per ore finché lo staff non ha
# lanciato !stato a mano — un guasto di firma deve auto-segnalarsi come i token.
_ALERT_COOLDOWN = 3600
_last_alert_ts = 0.0


async def _send_reject_alert() -> None:
    from notifications.token_health import _alert
    await _alert(
        f"🚨 **WEBHOOK META RESPINTO — firma non valida** (totale respinti: {_rejected_count})\n"
        "Il bot sta RIFIUTANDO messaggi in ingresso: i clienti di quel canale non ricevono "
        "risposte. Causa tipica: secret errato su Railway — META_APP_SECRET (app Meta/"
        "WhatsApp) o META_APP_SECRET_IG (Segreto dell'app Instagram). Lancia `!stato` per i dettagli."
    )


def _record_reject() -> None:
    global _rejected_count, _last_rejected_at, _last_alert_ts
    import asyncio
    import time
    _rejected_count += 1
    _last_rejected_at = time.strftime("%d/%m %H:%M", time.localtime())
    now = time.time()
    if now - _last_alert_ts >= _ALERT_COOLDOWN:
        _last_alert_ts = now
        try:
            asyncio.get_running_loop().create_task(_send_reject_alert())
        except RuntimeError:
            pass  # nessun event loop (test sincroni): niente alert, solo contatore


def signature_reject_stats() -> tuple[int, str]:
    """(numero webhook respinti per firma, orario dell'ultimo)."""
    return _rejected_count, _last_rejected_at


async def verify_meta_signature(request: Request) -> bytes:
    """Legge il body grezzo e verifica X-Hub-Signature-256.

    Ritorna i byte grezzi del body (da passare a json.loads).
    Solleva HTTPException(403) se la firma è mancante o non valida.
    Se META_APP_SECRET non è configurato, salta la verifica (log una volta).
    """
    raw = await request.body()
    # Due firmatari possibili sullo stesso endpoint: l'app Meta principale
    # (WhatsApp, META_APP_SECRET) e l'app Instagram Login (webhook IG, firmati
    # col "Segreto dell'app Instagram" → META_APP_SECRET_IG). La firma è valida
    # se corrisponde a UNO QUALSIASI dei secret configurati.
    secrets = [s for s in (settings.meta_app_secret, settings.meta_app_secret_ig) if s]
    if not secrets:
        global _warned_no_secret
        if not _warned_no_secret:
            logger.warning(
                "META_APP_SECRET non configurato — verifica firma webhook DISABILITATA. "
                "Imposta META_APP_SECRET in produzione."
            )
            _warned_no_secret = True
        return raw

    header = request.headers.get("X-Hub-Signature-256", "")
    if not header.startswith("sha256="):
        logger.warning("Webhook senza header X-Hub-Signature-256")
        _record_reject()
        raise HTTPException(status_code=403, detail="Firma mancante")

    received = header.split("=", 1)[1]
    for secret in secrets:
        expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, received):
            return raw

    logger.warning("Firma webhook non valida — richiesta respinta")
    _record_reject()
    raise HTTPException(status_code=403, detail="Firma non valida")
