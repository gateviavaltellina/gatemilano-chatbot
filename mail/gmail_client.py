"""Client Gmail minimale per la casella info@ (solo httpx, nessuna libreria Google).

Usa un refresh token OAuth della casella: il bot gira su Railway e non ha un browser,
quindi il consenso si dà una volta sola fuori e qui si rinnova solo l'access token.

Volutamente piccolo: leggere i messaggi non letti, rispondere nel thread, salvare una
bozza, mettere un'etichetta. Niente di più — ogni capacità in più su una casella
aziendale è una capacità in più che può sbagliare.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from email.message import EmailMessage
from email.utils import parseaddr

import httpx

from config import settings

logger = logging.getLogger(__name__)

_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_TOKEN_URL = "https://oauth2.googleapis.com/token"

# Access token in cache: dura ~1h, lo rinnoviamo con un margine.
_token_cache: dict = {"value": "", "exp": 0.0}


def configured() -> bool:
    return bool(settings.gmail_client_id
                and settings.gmail_client_secret
                and settings.gmail_refresh_token)


async def _access_token() -> str:
    if _token_cache["value"] and time.time() < _token_cache["exp"]:
        return _token_cache["value"]
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.post(_TOKEN_URL, data={
            "client_id": settings.gmail_client_id,
            "client_secret": settings.gmail_client_secret,
            "refresh_token": settings.gmail_refresh_token,
            "grant_type": "refresh_token",
        })
    r.raise_for_status()
    data = r.json()
    _token_cache["value"] = data["access_token"]
    _token_cache["exp"] = time.time() + int(data.get("expires_in", 3600)) - 120
    return _token_cache["value"]


async def _request(method: str, path: str, **kw):
    token = await _access_token()
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(kw.pop("headers", {}))
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        r = await client.request(method, f"{_API}{path}", headers=headers, **kw)
    r.raise_for_status()
    return r.json() if r.content else {}


# --- lettura -----------------------------------------------------------------

async def list_unprocessed(query: str, limit: int) -> list[str]:
    """ID dei messaggi che corrispondono alla query Gmail, dal più recente."""
    data = await _request("GET", "/messages", params={"q": query, "maxResults": limit})
    return [m["id"] for m in data.get("messages", [])]


def _header(payload: dict, name: str) -> str:
    for h in payload.get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _all_headers(payload: dict, name: str) -> list[str]:
    return [h.get("value", "") for h in payload.get("headers", [])
            if h.get("name", "").lower() == name.lower()]


def _walk_parts(payload: dict):
    yield payload
    for part in payload.get("parts", []) or []:
        yield from _walk_parts(part)


def _plain_body(payload: dict) -> str:
    """Testo del messaggio: preferisce text/plain, ripiega sull'HTML ripulito."""
    html = ""
    for part in _walk_parts(payload):
        mime = part.get("mimeType", "")
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        try:
            text = base64.urlsafe_b64decode(data + "==").decode("utf-8", "replace")
        except Exception:
            continue
        if mime == "text/plain":
            return text
        if mime == "text/html" and not html:
            html = text
    if html:
        html = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", html)
        return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()
    return ""


def _has_attachments(payload: dict) -> bool:
    return any(p.get("filename") for p in _walk_parts(payload))


async def get_message(message_id: str) -> dict:
    """Campi che servono al triage, già estratti."""
    raw = await _request("GET", f"/messages/{message_id}", params={"format": "full"})
    payload = raw.get("payload", {})
    sender = _header(payload, "From")
    return {
        "id": raw.get("id", ""),
        "thread_id": raw.get("threadId", ""),
        "label_ids": raw.get("labelIds", []) or [],
        "from": sender,
        "from_email": parseaddr(sender)[1].lower(),
        "to": _header(payload, "To"),
        "cc": _header(payload, "Cc"),
        # info@ è su Aruba e arriva in Gmail per inoltro: il Delivered-To è l'unico
        # segno affidabile che il messaggio è passato da quella casella.
        "delivered_to": [v.lower() for v in _all_headers(payload, "Delivered-To")],
        "subject": _header(payload, "Subject"),
        "message_id_header": _header(payload, "Message-ID"),
        "references": _header(payload, "References"),
        "list_unsubscribe": _header(payload, "List-Unsubscribe"),
        "precedence": _header(payload, "Precedence"),
        "auto_submitted": _header(payload, "Auto-Submitted"),
        "body": _plain_body(payload)[:8000],
        "has_attachments": _has_attachments(payload),
    }


async def thread_has_our_reply(thread_id: str) -> bool:
    """True se nel thread c'è già un messaggio inviato da noi.

    È la guardia anti-loop principale: non si risponde due volte alla stessa
    conversazione, e non si risponde a un thread che lo staff ha già gestito a mano.
    """
    data = await _request("GET", f"/threads/{thread_id}", params={"format": "metadata"})
    for msg in data.get("messages", []):
        if "SENT" in (msg.get("labelIds") or []):
            return True
    return False


# --- mittente ----------------------------------------------------------------

_send_as_cache: dict[str, tuple[bool, float]] = {}


async def can_send_as(address: str) -> bool:
    """True se l'account Gmail può spedire come `address` (alias "Invia come" verificato).

    La casella collegata è una Gmail personale: senza l'alias, Gmail riscriverebbe il
    mittente con l'indirizzo personale e il cliente vedrebbe quello. Meglio non inviare.
    """
    cached = _send_as_cache.get(address)
    if cached and time.time() < cached[1]:
        return cached[0]
    try:
        data = await _request("GET", f"/settings/sendAs/{address}")
        ok = data.get("verificationStatus", "accepted") == "accepted"
    except httpx.HTTPStatusError as e:
        if e.response.status_code != 404:
            raise
        ok = False
    _send_as_cache[address] = (ok, time.time() + 3600)
    return ok


# --- scrittura ---------------------------------------------------------------

def _build_raw(to: str, subject: str, body: str, in_reply_to: str, references: str) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["From"] = f"{settings.mail_from_name} <{settings.mail_reply_as}>"
    msg["Subject"] = subject if subject.lower().startswith("re:") else f"Re: {subject}"
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = (references + " " + in_reply_to).strip()
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


async def send_reply(msg: dict, body: str) -> str:
    raw = _build_raw(msg["from_email"], msg["subject"], body,
                     msg["message_id_header"], msg["references"])
    out = await _request("POST", "/messages/send",
                         json={"raw": raw, "threadId": msg["thread_id"]})
    return out.get("id", "")


async def create_draft(msg: dict, body: str) -> str:
    raw = _build_raw(msg["from_email"], msg["subject"], body,
                     msg["message_id_header"], msg["references"])
    out = await _request("POST", "/drafts",
                         json={"message": {"raw": raw, "threadId": msg["thread_id"]}})
    return out.get("id", "")


# --- etichette ---------------------------------------------------------------

_label_cache: dict[str, str] = {}


async def ensure_label(name: str) -> str:
    if name in _label_cache:
        return _label_cache[name]
    data = await _request("GET", "/labels")
    for lab in data.get("labels", []):
        if lab.get("name") == name:
            _label_cache[name] = lab["id"]
            return lab["id"]
    created = await _request("POST", "/labels", json={
        "name": name,
        "labelListVisibility": "labelShow",
        "messageListVisibility": "show",
    })
    _label_cache[name] = created["id"]
    return created["id"]


async def add_label(message_id: str, label_name: str) -> None:
    label_id = await ensure_label(label_name)
    await _request("POST", f"/messages/{message_id}/modify",
                   json={"addLabelIds": [label_id]})
