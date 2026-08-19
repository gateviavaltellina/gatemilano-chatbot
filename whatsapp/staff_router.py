"""Router sicuro dei DM staff verso il concierge operativo.

Meta continua a chiamare il webhook del chatbot clienti. Solo i messaggi 1:1
provenienti dall'allowlist staff (e gli status di consegna) vengono copiati in un
payload separato, rifirmati con l'App Secret Meta e inoltrati al servizio
concierge. Il payload filtrato impedisce che conversazioni cliente finiscano
nella memoria del concierge quando Meta invia un batch misto.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def normalize_wa_id(value: str) -> str:
    """Normalizza un numero/wa_id al formato numerico usato dai webhook Meta."""
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    return digits[2:] if digits.startswith("00") else digits


def parse_staff_phones(raw: str) -> set[str]:
    return {
        normalized
        for item in str(raw or "").split(",")
        if (normalized := normalize_wa_id(item))
    }


def _is_staff_dm(message: object, staff_phones: set[str]) -> bool:
    if not isinstance(message, dict) or message.get("group_id"):
        return False
    return normalize_wa_id(message.get("from") or "") in staff_phones


def build_staff_forward(
    payload: dict,
    staff_phones: set[str],
    app_secret: str,
) -> tuple[bytes, str] | None:
    """Crea il solo sotto-payload staff/status e una firma Meta-compatible.

    Ritorna ``None`` se il batch non contiene nulla da inoltrare o se manca il
    secret: in entrambi i casi il router resta fail-closed per i DM staff.
    """
    if payload.get("object") != "whatsapp_business_account" or not app_secret:
        return None

    routed_entries: list[dict] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        routed_changes: list[dict] = []
        for change in entry.get("changes") or []:
            if not isinstance(change, dict):
                continue
            value = change.get("value") or {}
            if not isinstance(value, dict):
                continue

            staff_messages = [
                message
                for message in (value.get("messages") or [])
                if _is_staff_dm(message, staff_phones)
            ]
            statuses = value.get("statuses") or []
            if not staff_messages and not statuses:
                continue

            routed_value = copy.deepcopy(value)
            if "messages" in routed_value:
                routed_value["messages"] = copy.deepcopy(staff_messages)
            routed_change = copy.deepcopy(change)
            routed_change["value"] = routed_value
            routed_changes.append(routed_change)

        if routed_changes:
            routed_entry = copy.deepcopy(entry)
            routed_entry["changes"] = routed_changes
            routed_entries.append(routed_entry)

    if not routed_entries:
        return None

    routed_payload = copy.deepcopy(payload)
    routed_payload["entry"] = routed_entries
    raw = json.dumps(routed_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    signature = "sha256=" + hmac.new(app_secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
    return raw, signature


def valid_staff_webhook_url(value: str) -> bool:
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
        and parsed.path.rstrip("/").endswith("/webhook")
    )


async def forward_staff_webhook(
    webhook_url: str,
    raw: bytes,
    signature: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Inoltra best-effort senza loggare payload, telefoni o credenziali."""
    if not valid_staff_webhook_url(webhook_url):
        logger.error("Router staff disabilitato: URL concierge non valido")
        return False

    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=10)
    try:
        response = await http.post(
            webhook_url,
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-Hub-Signature-256": signature,
                "X-Gate-Webhook-Source": "customer-router",
            },
        )
        if response.status_code >= 400:
            logger.error("Router staff: concierge ha rifiutato il webhook (HTTP %s)", response.status_code)
            return False
        return True
    except Exception as exc:
        logger.error("Router staff: inoltro al concierge fallito (%s)", type(exc).__name__)
        return False
    finally:
        if owns_client:
            await http.aclose()
