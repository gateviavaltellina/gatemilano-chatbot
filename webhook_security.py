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


async def verify_meta_signature(request: Request) -> bytes:
    """Legge il body grezzo e verifica X-Hub-Signature-256.

    Ritorna i byte grezzi del body (da passare a json.loads).
    Solleva HTTPException(403) se la firma è mancante o non valida.
    Se META_APP_SECRET non è configurato, salta la verifica (log una volta).
    """
    raw = await request.body()
    secret = settings.meta_app_secret
    if not secret:
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
        raise HTTPException(status_code=403, detail="Firma mancante")

    expected = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    received = header.split("=", 1)[1]
    if not hmac.compare_digest(expected, received):
        logger.warning("Firma webhook non valida — richiesta respinta")
        raise HTTPException(status_code=403, detail="Firma non valida")

    return raw
