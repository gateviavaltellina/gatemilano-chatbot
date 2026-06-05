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


def _webhook_url_for(phone: str) -> str:
    is_ig = phone.startswith("ig:")
    if is_ig and settings.discord_ig_webhook_url:
        return settings.discord_ig_webhook_url.split("?")[0] + "?wait=true"
    return settings.discord_webhook_url.split("?")[0] + "?wait=true"


async def notify_conversation(phone: str, venue: str, user_msg: str, bot_reply: str, context: dict = None) -> None:
    if not settings.discord_webhook_url and not settings.discord_ig_webhook_url:
        return
    url = _webhook_url_for(phone)
    if not url or url.startswith("?"):
        return
    is_ig = phone.startswith("ig:")
    emoji = VENUE_EMOJI.get(venue or "", "❓")
    source = "📸 IG" if is_ig else "💬 WA"
    venue_label = {"gate_milano": "Gate Milano", "gate_sardinia": "Gate Sardinia"}.get(venue or "", "Venue sconosciuto")
    masked = _mask_phone(phone)
    payload = {
        "embeds": [
            {
                "color": 0xE1306C if is_ig else 0x7C3AED,
                "description": f"{emoji} {venue_label} · {source} · {masked}",
                "fields": [
                    {"name": "👤 Utente", "value": user_msg[:1024] or "​", "inline": False},
                    {"name": "🤖 Bot", "value": bot_reply[:1024] or "​", "inline": False},
                ],
            }
        ]
    }
    # ?wait=true → Discord restituisce il messaggio con l'ID (necessario per human takeover)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            msg_id = r.json().get("id")
            if msg_id:
                from notifications.discord_bot import register_message
                register_message(msg_id, phone, context)
        except Exception as e:
            logger.warning("Discord notify failed: %s", e)


async def notify_human_message(phone: str, venue: str, user_msg: str, context: dict = None) -> None:
    """Notifica Discord quando il bot è in pausa (human takeover)."""
    if not settings.discord_webhook_url and not settings.discord_ig_webhook_url:
        return
    emoji = VENUE_EMOJI.get(venue or "", "❓")
    venue_label = {"gate_milano": "Gate Milano", "gate_sardinia": "Gate Sardinia"}.get(venue or "", "Venue sconosciuto")
    masked = _mask_phone(phone)
    payload = {
        "embeds": [
            {
                "color": 0xF59E0B,
                "description": f"{emoji} {venue_label} · {masked} — ⏸️ STAFF MODE",
                "fields": [
                    {"name": "👤 Utente", "value": user_msg[:1024] or "​", "inline": False},
                    {"name": "ℹ️ Azioni", "value": "Rispondi con `!r <testo>` oppure `!rel` per riattivare il bot.", "inline": False},
                ],
            }
        ]
    }
    url = _webhook_url_for(phone)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            msg_id = r.json().get("id")
            if msg_id:
                from notifications.discord_bot import register_message
                register_message(msg_id, phone, context)
        except Exception as e:
            logger.warning("Discord notify_human failed: %s", e)


async def notify_escalation(
    phone: str, venue: str, user_msg: str, categories: list, context: dict = None
) -> None:
    """Alert prominente allo staff quando un messaggio tocca un tema sensibile
    (accessibilità, rimborsi, salute, reclami). Il bot risponde comunque; questo
    serve a far intervenire un umano in fretta. Registra l'ID così lo staff può
    prendere in carico con !t / !r direttamente in reply."""
    if not settings.discord_webhook_url and not settings.discord_ig_webhook_url:
        return
    is_ig = phone.startswith("ig:")
    emoji = VENUE_EMOJI.get(venue or "", "❓")
    source = "📸 IG" if is_ig else "💬 WA"
    venue_label = {"gate_milano": "Gate Milano", "gate_sardinia": "Gate Sardinia"}.get(venue or "", "Venue sconosciuto")
    masked = _mask_phone(phone)
    cats = " · ".join(categories) if categories else "tema sensibile"
    payload = {
        "content": "🚨 **ATTENZIONE STAFF** — messaggio sensibile, valuta presa in carico",
        "embeds": [
            {
                "color": 0xDC2626,
                "description": f"🚨 {cats}\n{emoji} {venue_label} · {source} · {masked}",
                "fields": [
                    {"name": "👤 Utente", "value": user_msg[:1024] or "​", "inline": False},
                    {"name": "ℹ️ Azioni", "value": "`!t` per prendere in carico · `!r <testo>` per rispondere a mano.", "inline": False},
                ],
            }
        ],
    }
    url = _webhook_url_for(phone)
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.post(url, json=payload)
            r.raise_for_status()
            msg_id = r.json().get("id")
            if msg_id:
                from notifications.discord_bot import register_message
                register_message(msg_id, phone, context)
        except Exception as e:
            logger.warning("Discord notify_escalation failed: %s", e)
