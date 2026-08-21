"""Client firmato per il Concierge canonico dell'admin Gate Milano.

Il chatbot clienti possiede il webhook Meta. I DM dei numeri esplicitamente
abilitati vengono convertiti nel contratto minimale del Concierge, firmati con
un secret dedicato e inviati all'admin. Il client non logga mai testo, numeri o
segreti.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

_MAX_REPLY_CHARS = 12_000
_WHATSAPP_CHUNK_CHARS = 3_500


def valid_concierge_bridge_url(value: str) -> bool:
    try:
        parsed = urlparse(value)
    except ValueError:
        return False
    return (
        parsed.scheme == "https"
        and bool(parsed.hostname)
        and parsed.username is None
        and parsed.password is None
        and not parsed.query
        and not parsed.fragment
        and parsed.path.rstrip("/").endswith("/api/agent/channels/whatsapp")
    )


def build_concierge_request(
    *,
    message_id: str,
    sender_wa_id: str,
    phone_number_id: str,
    text: str,
) -> bytes:
    payload = {
        "version": 1,
        "messageId": message_id,
        "senderWaId": sender_wa_id,
        "externalConversationId": sender_wa_id,
        "phoneNumberId": phone_number_id,
        "isGroup": False,
        "text": text,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def sign_concierge_request(raw: bytes, secret: str, timestamp: str) -> str:
    signed = timestamp.encode("ascii") + b"." + raw
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def split_whatsapp_reply(text: str, max_chars: int = _WHATSAPP_CHUNK_CHARS) -> list[str]:
    """Divide senza spezzare parole quando la risposta supera il limite WA."""
    rest = text.strip()
    if not rest:
        return []
    chunks: list[str] = []
    while len(rest) > max_chars:
        window = rest[: max_chars + 1]
        newline = window.rfind("\n")
        space = window.rfind(" ")
        minimum_natural_split = int(max_chars * 0.6)
        if newline >= minimum_natural_split:
            split_at = newline
        elif space >= minimum_natural_split:
            split_at = space
        else:
            split_at = max_chars
        chunks.append(rest[:split_at].strip())
        rest = rest[split_at:].strip()
    if rest:
        chunks.append(rest)
    return chunks


async def request_concierge_reply(
    *,
    bridge_url: str,
    bridge_secret: str,
    message_id: str,
    sender_wa_id: str,
    phone_number_id: str,
    text: str,
    client: httpx.AsyncClient | None = None,
    now_seconds: int | None = None,
) -> str | None:
    """Restituisce la risposta canonica oppure ``None`` in caso di errore."""
    secret = bridge_secret.strip()
    if not secret or not valid_concierge_bridge_url(bridge_url):
        logger.error("Concierge WhatsApp non configurato: URL o secret mancante")
        return None

    raw = build_concierge_request(
        message_id=message_id,
        sender_wa_id=sender_wa_id,
        phone_number_id=phone_number_id,
        text=text,
    )
    timestamp = str(now_seconds if now_seconds is not None else int(time.time()))
    signature = sign_concierge_request(raw, secret, timestamp)
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=240)
    try:
        response = await http.post(
            bridge_url,
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Gate-Bridge-Timestamp": timestamp,
                "X-Gate-Bridge-Signature": signature,
            },
        )
        if response.status_code >= 400:
            logger.error("Concierge WhatsApp ha rifiutato la richiesta (HTTP %s)", response.status_code)
            return None
        try:
            payload = response.json()
        except ValueError:
            logger.error("Concierge WhatsApp ha restituito JSON non valido")
            return None
        reply = payload.get("reply") if isinstance(payload, dict) else None
        status = payload.get("status") if isinstance(payload, dict) else None
        if (
            status != "completed"
            or not isinstance(reply, str)
            or not reply.strip()
            or len(reply) > _MAX_REPLY_CHARS
        ):
            logger.error("Concierge WhatsApp ha restituito una risposta non valida")
            return None
        return reply.strip()
    except Exception as exc:
        logger.error("Concierge WhatsApp non raggiungibile (%s)", type(exc).__name__)
        return None
    finally:
        if owns_client:
            await http.aclose()
