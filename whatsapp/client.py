import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)

def _wa_base() -> str:
    return f"{settings.wa_api_url}/{settings.wa_phone_number_id}"

async def send_message(to: str, text: str) -> bool:
    headers = {
        "Authorization": f"Bearer {settings.wa_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{_wa_base()}/messages", headers=headers, json=payload)
            r.raise_for_status()
            logger.info("Messaggio inviato a %s", to)
            return True
        except httpx.HTTPStatusError as e:
            logger.error("Errore invio WhatsApp a %s: %s — %s", to, e, e.response.text)
            return False
        except Exception as e:
            logger.error("Errore invio WhatsApp: %s", e)
            return False


async def send_group_message(group_id: str, text: str) -> bool:
    """Invia un messaggio di testo a un gruppo WhatsApp (Cloud API Groups).
    Stesso endpoint dei DM, ma recipient_type='group' e to=<GROUP_ID>."""
    headers = {
        "Authorization": f"Bearer {settings.wa_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "group",
        "to": group_id,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{_wa_base()}/messages", headers=headers, json=payload)
            r.raise_for_status()
            logger.info("Messaggio inviato al gruppo %s", group_id[:16])
            return True
        except httpx.HTTPStatusError as e:
            logger.error("Errore invio gruppo %s: %s — %s", group_id[:16], e, e.response.text)
            return False
        except Exception as e:
            logger.error("Errore invio gruppo: %s", e)
            return False

async def send_document(to: str, url: str, filename: str, caption: str = "") -> bool:
    headers = {
        "Authorization": f"Bearer {settings.wa_access_token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "document",
        "document": {"link": url, "filename": filename, "caption": caption},
    }
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{_wa_base()}/messages", headers=headers, json=payload)
            r.raise_for_status()
            logger.info("Documento inviato a %s: %s", to, filename)
            return True
        except httpx.HTTPStatusError as e:
            logger.error("Errore invio documento a %s: %s — %s", to, e, e.response.text)
            return False
        except Exception as e:
            logger.error("Errore invio documento: %s", e)
            return False


async def mark_as_read(message_id: str) -> None:
    headers = {
        "Authorization": f"Bearer {settings.wa_access_token}",
        "Content-Type": "application/json",
    }
    payload = {"messaging_product": "whatsapp", "status": "read", "message_id": message_id}
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            await client.post(f"{_wa_base()}/messages", headers=headers, json=payload)
        except Exception:
            pass
