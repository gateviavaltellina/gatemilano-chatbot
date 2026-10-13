"""Verifica dei biglietti su Xceed per le richieste di rimborso (Partner API, sola lettura).

Cosa si può sapere di un biglietto (docs.xceed.me, "Partner Tickets API"): evento,
intestatario, email dell'acquirente, codice prenotazione e QR, prezzo, se è stato
usato all'ingresso (`hasCheckedIn`) e se è ancora valido (`isActive`).

Al Gate la selezione avviene PRIMA della scansione (staff 13/10): chi è stato
respinto ha il biglietto "non usato". Un biglietto usato significa che il cliente è
entrato, quindi niente rimborso.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx

from config import settings

logger = logging.getLogger(__name__)

_PARTNER = "https://partner.xceed.me"
_PAGE = 200
_MAX_PAGES = 40

# Biglietti per evento, in cache: un Perreo ha migliaia di biglietti e più richieste
# della stessa serata arrivano a pochi minuti l'una dall'altra.
_tickets_cache: dict[str, tuple[list[dict], float]] = {}


def _headers() -> dict:
    return {"X-API-Key": settings.xceed_api_key, "Accept": "application/json"}


async def recent_events(days: int) -> list[dict]:
    """Eventi Xceed iniziati negli ultimi `days` giorni (già passati)."""
    now = int(time.time())
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
        r = await client.get(f"{_PARTNER}/v1/events", headers=_headers(),
                             params={"startingTime": now - days * 86400, "limit": 200})
        r.raise_for_status()
        events = r.json().get("data", []) or []
    return [e for e in events if int(e.get("startingTime") or 0) <= now]


async def event_tickets(event: dict) -> list[dict]:
    key = str(event.get("uuid") or event.get("id"))
    cached = _tickets_cache.get(key)
    if cached and time.time() < cached[1]:
        return cached[0]
    tickets: list[dict] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        # La documentazione non dice se `events` vuole l'uuid o l'id numerico: si
        # prova l'uuid e, se non torna nulla, l'id.
        for ev_id in [v for v in (event.get("uuid"), event.get("id")) if v]:
            tickets = []
            for page in range(_MAX_PAGES):
                r = await client.get(f"{_PARTNER}/v1/tickets", headers=_headers(), params={
                    "events": ev_id, "limit": _PAGE, "offset": page * _PAGE})
                if r.status_code >= 400:
                    break
                batch = r.json().get("data", []) or []
                tickets.extend(batch)
                if len(batch) < _PAGE:
                    break
            if tickets:
                break
    _tickets_cache[key] = (tickets, time.time() + 600)
    return tickets


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().lower())


def _ticket_codes(t: dict) -> set[str]:
    b = t.get("booking") or {}
    return {str(v).lower() for v in (t.get("qrCode"), b.get("bookingId"), b.get("bookingUuid"),
                                      b.get("paymentId")) if v}


def _ticket_emails(t: dict) -> set[str]:
    b = t.get("booking") or {}
    return {_norm(v) for v in (t.get("email"), b.get("email")) if v}


def _ticket_names(t: dict) -> set[str]:
    b = t.get("booking") or {}
    return {_norm(f"{p.get('firstName','')} {p.get('lastName','')}") for p in (t, b)} - {""}


def match_tickets(tickets: list[dict], emails: set[str], codes: set[str],
                  names: set[str]) -> tuple[list[dict], str]:
    """Biglietti della richiesta e come sono stati trovati ("codice", "email", "nome").

    Codice ed email sono prove forti; il nome da solo è un indizio (omonimi), e lo
    si segnala come tale allo staff.
    """
    emails = {_norm(e) for e in emails if e}
    codes = {str(c).strip().lower() for c in codes if c}
    names = {_norm(n) for n in names if n}
    by_code = [t for t in tickets if _ticket_codes(t) & codes]
    if by_code:
        return _whole_bookings(tickets, by_code), "codice"
    by_email = [t for t in tickets if _ticket_emails(t) & emails]
    if by_email:
        return _whole_bookings(tickets, by_email), "email"
    by_name = [t for t in tickets if _ticket_names(t) & names]
    if by_name:
        return by_name, "nome"
    return [], ""


def _whole_bookings(tickets: list[dict], found: list[dict]) -> list[dict]:
    """Tutti i biglietti delle prenotazioni trovate (chi compra per il gruppo li ha tutti)."""
    ids = {(t.get("booking") or {}).get("bookingId") for t in found} - {None}
    if not ids:
        return found
    return [t for t in tickets if (t.get("booking") or {}).get("bookingId") in ids]


async def find_request_tickets(emails: set[str], codes: set[str], names: set[str],
                               date_hint: str = "") -> dict:
    """Cerca i biglietti della richiesta negli eventi recenti.

    Ritorna {"event": {...}, "tickets": [...], "match": "..."} oppure {} se non trovati.
    Con una data indicata dal cliente si guarda prima quella serata.
    """
    if not settings.xceed_api_key:
        return {"error": "XCEED_API_KEY mancante"}
    try:
        events = await recent_events(settings.refund_window_days + 4)
    except Exception as e:
        logger.warning("Eventi Xceed non leggibili: %s", e)
        return {"error": f"eventi Xceed non leggibili: {str(e)[:120]}"}

    def _day(e):
        return event_day(e)

    events.sort(key=lambda e: (_day(e) != date_hint, -int(e.get("startingTime") or 0)))
    weak: dict = {}
    for ev in events:
        try:
            tickets = await event_tickets(ev)
        except Exception as e:
            logger.warning("Biglietti Xceed non leggibili per %s: %s", ev.get("name"), e)
            continue
        found, how = match_tickets(tickets, emails, codes, names)
        if found and how != "nome":
            return {"event": ev, "tickets": found, "match": how}
        if found and not weak:
            weak = {"event": ev, "tickets": found, "match": how}
    return weak


def event_day(event: dict) -> str:
    """Data della serata a Milano (YYYY-MM-DD)."""
    ts = int(event.get("startingTime") or 0)
    return datetime.fromtimestamp(ts, ZoneInfo("Europe/Rome")).strftime("%Y-%m-%d")


def summarize(tickets: list[dict]) -> dict:
    """Quanti biglietti, se usati, se ancora validi, intestatari, prezzo."""
    used = [t for t in tickets if t.get("hasCheckedIn")]
    inactive = [t for t in tickets if t.get("isActive") is False]
    holders = sorted({f"{t.get('firstName','')} {t.get('lastName','')}".strip() for t in tickets})
    booking_ids = sorted({str((t.get("booking") or {}).get("bookingId")) for t in tickets
                          if (t.get("booking") or {}).get("bookingId")})
    price = ""
    if tickets:
        p = ((tickets[0].get("offer") or {}).get("price") or {})
        if p.get("amount"):
            price = f"{p.get('amount')} {p.get('currency', '')}".strip()
    return {"count": len(tickets), "used": len(used), "inactive": len(inactive),
            "holders": holders, "booking_ids": booking_ids, "price": price,
            "offer": (tickets[0].get("offer") or {}).get("name", "") if tickets else ""}
