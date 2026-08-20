import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)

def _wa_base() -> str:
    return f"{settings.wa_api_url}/{settings.wa_phone_number_id}"

# Ultimo errore di INVIO WhatsApp (con orario): diagnosi visibile via !stato e
# relay, senza leggere i log Railway (speculare a instagram.client.last_send_error).
_last_send_error: str = ""


def _record_send_error(detail: str) -> None:
    global _last_send_error
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ts = datetime.now(ZoneInfo("Europe/Rome")).strftime("%d/%m %H:%M")
    _last_send_error = f"{ts} — {detail}"


def last_send_error() -> str:
    """Ultimo errore di invio WhatsApp registrato ('' se nessuno)."""
    return _last_send_error


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
            _record_send_error(f"HTTP {e.response.status_code}: {e.response.text[:400]}")
            return False
        except Exception as e:
            logger.error("Errore invio WhatsApp: %s", e)
            _record_send_error(f"{type(e).__name__}: {e}")
            return False


async def create_group(subject: str, description: str = "", join_approval_mode: str = "") -> dict:
    """Crea un gruppo WhatsApp (Cloud API Groups), di proprietà del numero business.
    Ritorna la risposta API (contiene l'id del gruppo). L'invite_link da mandare
    allo staff arriva poi via webhook. Ritorna {} in caso di errore."""
    headers = {
        "Authorization": f"Bearer {settings.wa_access_token}",
        "Content-Type": "application/json",
    }
    payload = {"messaging_product": "whatsapp", "subject": subject}
    if description:
        payload["description"] = description
    if join_approval_mode:
        payload["join_approval_mode"] = join_approval_mode
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.post(f"{_wa_base()}/groups", headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
            logger.info("Gruppo creato: %s", data)
            return data
        except httpx.HTTPStatusError as e:
            logger.error("Errore creazione gruppo: %s — %s", e, e.response.text)
            return {}
        except Exception as e:
            logger.error("Errore creazione gruppo: %s", e)
            return {}


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


async def get_media_url(media_id: str) -> str | None:
    """Risolve l'URL di download di un media ricevuto in chat (foto del cliente).
    L'URL restituito da Meta scade in ~5 minuti e il download richiede lo stesso
    header Authorization. Ritorna None su errore (il chiamante ricade sul testo)."""
    if not media_id:
        return None
    headers = {"Authorization": f"Bearer {settings.wa_access_token}"}
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            r = await client.get(f"{settings.wa_api_url}/{media_id}", headers=headers)
            r.raise_for_status()
            return r.json().get("url") or None
        except httpx.HTTPStatusError as e:
            logger.error("Errore risoluzione media %s: %s — %s", media_id, e, e.response.text)
            return None
        except Exception as e:
            logger.error("Errore risoluzione media %s: %s", media_id, e)
            return None


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
