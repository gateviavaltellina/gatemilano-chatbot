import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)


_MILANO_IDS = {"35517015101275600", "17841405933946552"}
_SARDINIA_IDS = {"24588954374135134", "17841452139166980"}

def _token_for_account(ig_account_id: str) -> str:
    if ig_account_id in _MILANO_IDS:
        return settings.ig_gatemilano_token
    if ig_account_id in _SARDINIA_IDS:
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
                f"{settings.ig_api_url}/{ig_account_id}/messages",
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


async def react_to_message(ig_account_id: str, recipient_id: str, message_id: str, reaction: str = "love") -> bool:
    """Mette una reaction (emoji) a un messaggio IG, senza inviare testo.
    Usato per menzioni/post nelle storie e per le reaction.
    API Meta: POST /{ig_id}/messages con sender_action=react."""
    token = _token_for_account(ig_account_id)
    if not token:
        logger.warning("Nessun token IG per account %s", ig_account_id)
        return False
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {
        "recipient": {"id": recipient_id},
        "sender_action": "react",
        "payload": {"message_id": message_id, "reaction": reaction},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(
                f"{settings.ig_api_url}/{ig_account_id}/messages",
                headers=headers,
                json=payload,
            )
            r.raise_for_status()
            logger.info("IG reaction '%s' a %s (msg %s)", reaction, recipient_id, message_id[:14])
            return True
        except httpx.HTTPStatusError as e:
            logger.error("Errore reaction IG a %s: %s — %s", recipient_id, e, e.response.text)
            return False
        except Exception as e:
            logger.error("Errore reaction IG: %s", e)
            return False
