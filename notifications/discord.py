import httpx
import logging
from config import settings

logger = logging.getLogger(__name__)

VENUE_EMOJI = {
    "gate_milano": "🏙️",
    "gate_sardinia": "🏖️",
}


def _mask_phone(phone: str) -> str:
    if len(phone) > 6:
        return phone[:4] + "****" + phone[-3:]
    return "****"


async def notify_conversation(phone: str, venue: str, user_msg: str, bot_reply: str) -> None:
    if not settings.discord_webhook_url:
        return
    emoji = VENUE_EMOJI.get(venue or "", "❓")
    venue_label = {"gate_milano": "Gate Milano", "gate_sardinia": "Gate Sardinia"}.get(venue or "", "Venue sconosciuto")
    masked = _mask_phone(phone)
    payload = {
        "embeds": [
            {
                "color": 0x7C3AED,
                "fields": [
                    {"name": f"{emoji} {venue_label} · {masked}", "value": "", "inline": False},
                    {"name": "👤 Utente", "value": user_msg[:1024], "inline": False},
                    {"name": "🤖 Bot", "value": bot_reply[:1024], "inline": False},
                ],
            }
        ]
    }
    # ?wait=true → Discord restituisce il messaggio con l'ID (necessario per human takeover)
    url = settings.discord_webhook_url.split("?")[0] + "?wait=true"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            msg_id = r.json().get("id")
            if msg_id:
                from notifications.discord_bot import register_message
                register_message(msg_id, phone)
        except Exception as e:
            logger.warning("Discord notify failed: %s", e)


async def notify_human_message(phone: str, venue: str, user_msg: str) -> None:
    """Notifica Discord quando il bot è in pausa (human takeover)."""
    if not settings.discord_webhook_url:
        return
    emoji = VENUE_EMOJI.get(venue or "", "❓")
    venue_label = {"gate_milano": "Gate Milano", "gate_sardinia": "Gate Sardinia"}.get(venue or "", "Venue sconosciuto")
    masked = _mask_phone(phone)
    payload = {
        "embeds": [
            {
                "color": 0xF59E0B,
                "fields": [
                    {"name": f"{emoji} {venue_label} · {masked} — ⏸️ STAFF MODE", "value": "", "inline": False},
                    {"name": "👤 Utente", "value": user_msg[:1024], "inline": False},
                    {"name": "", "value": "Rispondi con `!reply <testo>` oppure `!release` per riattivare il bot.", "inline": False},
                ],
            }
        ]
    }
    url = settings.discord_webhook_url.split("?")[0] + "?wait=true"
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            msg_id = r.json().get("id")
            if msg_id:
                from notifications.discord_bot import register_message
                register_message(msg_id, phone)
        except Exception as e:
            logger.warning("Discord notify_human failed: %s", e)
