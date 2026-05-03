import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)


def _token_for_account(ig_account_id: str) -> str:
    if ig_account_id == settings.ig_gatemilano_id:
        return settings.ig_gatemilano_token
    if ig_account_id == settings.ig_gatesardinia_id:
        return settings.ig_gatesardinia_token
    return ""


async def send_ig_message(ig_account_id: str, recipient_id: str, text: str) -> bool:
    token = _token_for_account(ig_account_id)
    if not token:
        logger.warning("Nessun token IG per account %s", ig_account_id)
        return False
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                f"{settings.wa_api_url}/{ig_account_id}/messages",
                headers=headers,
                json=payload,
            )
            r.raise_for_status()
            logger.info("IG reply inviato a %s via account %s", recipient_id, ig_account_id)
            return True
        except httpx.HTTPStatusError as e:
            logger.error("Errore invio IG a %s: %s — %s", recipient_id, e, e.response.text)
            return False
        except Exception as e:
            logger.error("Errore invio IG: %s", e)
            return False
