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


_events_cache: dict = {"events": [], "exp": 0.0}


async def _all_events() -> list[dict]:
    """Tutti gli eventi visibili alla chiave, letti a pagine e tenuti in cache 6 ore.

    La Partner API ignora il filtro `startingTime` e restituisce gli eventi dal più
    vecchio (2018…): leggendo solo la prima pagina non si arrivava mai alle serate
    recenti, e ogni richiesta di rimborso risultava "biglietti non trovati" (13/10).
    """
    if _events_cache["events"] and time.time() < _events_cache["exp"]:
        return _events_cache["events"]
    events: list[dict] = []
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
        for _ in range(_MAX_PAGES * 2):
            r = await client.get(f"{_PARTNER}/v1/events", headers=_headers(),
                                 params={"offset": len(events), "limit": _PAGE})
            r.raise_for_status()
            batch = r.json().get("data", []) or []
            if not batch:
                break
            events.extend(batch)
    _events_cache.update(events=events, exp=time.time() + 6 * 3600)
    return events


async def recent_events(days: int) -> list[dict]:
    """Eventi Xceed iniziati negli ultimi `days` giorni (già passati), dal più recente."""
    now = int(time.time())
    since = now - days * 86400
    events = [e for e in await _all_events()
              if since <= int(e.get("startingTime") or 0) <= now]
    return sorted(events, key=lambda e: -int(e.get("startingTime") or 0))


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
    by_name = [t for t in tickets if _ticket_names(t) & names or _same_person(t, names)]
    if by_name:
        return by_name, "nome"
    return [], ""


def _whole_bookings(tickets: list[dict], found: list[dict]) -> list[dict]:
    """Tutti i biglietti delle prenotazioni trovate (chi compra per il gruppo li ha tutti)."""
    ids = {(t.get("booking") or {}).get("bookingId") for t in found} - {None}
    if not ids:
        return found
    return [t for t in tickets if (t.get("booking") or {}).get("bookingId") in ids]


# --- ricerca tramite MCP (Xceed Pro: accesso completo a tutte le vendite) -----------
#
# La Partner API può vedere solo una parte delle vendite (caso reale 13/10: ordine
# 24698564 del Perreo del 10/10, acquistato in app, non trovato). L'MCP di Xceed ha
# lo strumento `bookings_search`, che cerca per nome, email o codice e restituisce lo
# stato di rimborso e check-in: è la fonte più affidabile quando è collegato.

_LIST_KEYS = ("data", "items", "bookings", "results", "rows", "content", "result")
_mcp_event_id_form: dict[str, str] = {}   # "uuid" o "id": quale forma accetta bookings_search


def _as_list(data) -> list[dict]:
    if isinstance(data, list):
        return [d for d in data if isinstance(d, dict)]
    if isinstance(data, dict):
        for k in _LIST_KEYS:
            if isinstance(data.get(k), list):
                return [d for d in data[k] if isinstance(d, dict)]
            if isinstance(data.get(k), dict):
                inner = _as_list(data[k])
                if inner:
                    return inner
    return []


def _first(d: dict, *keys):
    for k in keys:
        cur = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
        if cur not in (None, ""):
            return cur
    return None


def booking_to_ticket(b: dict) -> dict:
    """Prenotazione MCP → stesso formato dei biglietti Partner API (per summarize/assess)."""
    legacy = _first(b, "legacyId", "bookingLegacyId", "booking.legacyId")
    if legacy is None and str(_first(b, "id") or "").isdigit():
        legacy = _first(b, "id")
    refunded = bool(_first(b, "isRefunded", "refunded", "refund.isRefunded")) or \
        "refund" in str(_first(b, "status", "refundStatus") or "").lower()
    checked = _first(b, "hasCheckedIn", "checkedIn", "isCheckedIn")
    if checked is None:
        checked = (_first(b, "checkedInCount", "checkins", "checkedInTickets") or 0) > 0
    return {
        "firstName": _first(b, "buyer.firstName", "firstName", "buyerFirstName") or "",
        "lastName": _first(b, "buyer.lastName", "lastName", "buyerLastName") or "",
        "email": _first(b, "buyer.email", "email", "buyerEmail") or "",
        "hasCheckedIn": bool(checked),
        "isActive": not refunded,
        "offer": {"name": _first(b, "offer.name", "offerName") or "",
                  "price": {"amount": _first(b, "price.amount", "totalPrice", "price", "amount") or "",
                            "currency": _first(b, "price.currency", "currency") or ""}},
        "booking": {"bookingId": legacy, "email": _first(b, "buyer.email", "email") or ""},
    }


async def _mcp_bookings(event: dict, term: str, errors: list[str] | None = None) -> list[dict]:
    from mail import xceed_mcp
    forms = [("uuid", event.get("uuid")), ("id", event.get("id"))]
    known = _mcp_event_id_form.get("form")
    forms.sort(key=lambda f: f[0] != known)
    failures = []
    for form, ev_id in forms:
        if not ev_id:
            continue
        try:
            data = await xceed_mcp.tool_json("bookings_search", {
                "eventId": str(ev_id), "search": term, "limit": 10})
        except Exception as e:
            logger.debug("bookings_search %s=%s fallita: %s", form, ev_id, e)
            failures.append(str(e))
            continue
        _mcp_event_id_form["form"] = form
        return _as_list(data)
    if failures and errors is not None:
        # Prima un errore qui diventava "biglietti non trovati" (caso reale 24/10).
        errors.append(f"MCP {event.get('name', '')}: {failures[0][:100]}")
    return []


def _name_tokens(name: str) -> list[str]:
    return [t for t in _norm(name).split(" ") if len(t) >= 3]


def _same_person(t: dict, names: set[str]) -> bool:
    """Il biglietto è di una delle persone cercate? (tutte le parti del nome, in qualsiasi ordine)"""
    holder = set(_norm(f"{t.get('firstName', '')} {t.get('lastName', '')}").split(" "))
    return any(set(_name_tokens(n)) <= holder for n in names if _name_tokens(n))


def _passes(detail) -> list[dict]:
    """I singoli biglietti (passes) nel dettaglio di una prenotazione, in qualsiasi involucro."""
    if not isinstance(detail, dict):
        return []
    for key in ("passes", "tickets"):
        if isinstance(detail.get(key), list):
            return [p for p in detail[key] if isinstance(p, dict)]
    for key in ("data", "booking", "result", "item"):
        inner = _passes(detail.get(key))
        if inner:
            return inner
    return []


def booking_tickets(b: dict, detail=None) -> list[dict]:
    """Prenotazione MCP → un biglietto per pass, ognuno col suo codice e il suo check-in.

    Serve ai rimborsi parziali (staff 17/10): in un gruppo alcuni entrano e altri no, e
    si rimborsano solo i biglietti non usati. Senza dettaglio resta un solo "biglietto"
    per prenotazione, con lo stato complessivo.
    """
    base = booking_to_ticket(b)
    passes = _passes(detail)
    if not passes:
        return [base]
    out = []
    for p in passes:
        t = dict(base)
        t["firstName"] = p.get("firstName") or base["firstName"]
        t["lastName"] = p.get("lastName") or base["lastName"]
        t["email"] = p.get("email") or base["email"]
        t["hasCheckedIn"] = bool(_first(p, "hasCheckedIn", "checkedIn", "isCheckedIn"))
        active = _first(p, "isActive", "active")
        t["isActive"] = base["isActive"] if active is None else bool(active)
        t["qrCode"] = _first(p, "qrCode", "code", "ticketCode") or ""
        out.append(t)
    return out


async def _mcp_booking_detail(event: dict, b: dict):
    from mail import xceed_mcp
    ev_id = event.get(_mcp_event_id_form.get("form") or "uuid") or event.get("uuid")
    for key in ("id", "uuid", "legacyId"):
        if not b.get(key):
            continue
        try:
            return await xceed_mcp.tool_json("booking_get", {"eventId": str(ev_id),
                                                             "bookingId": str(b[key])})
        except Exception as e:
            logger.debug("booking_get %s=%s fallita: %s", key, b[key], e)
    return None


async def find_via_mcp(events: list[dict], emails: set[str], codes: set[str],
                       names: set[str], errors: list[str] | None = None) -> dict:
    terms = [("codice", c) for c in sorted(codes) if len(str(c)) >= 5] + \
            [("email", e) for e in sorted(emails) if "@" in e] + \
            [("nome", n) for n in sorted(names) if len(n) >= 5]
    # La ricerca di Xceed confronta un campo alla volta: "Nome Cognome" intero spesso
    # non trova nulla. Si cerca anche per cognome (la parte più lunga) e si tiene solo
    # chi ha tutte le parti del nome.
    surnames = sorted({max(_name_tokens(n), key=len) for n in names if len(_name_tokens(n)) >= 2})
    for ev in events:
        for how, term in terms:
            found = await _mcp_bookings(ev, term, errors)
            if found:
                tickets = []
                for b in found:
                    tickets += booking_tickets(b, await _mcp_booking_detail(ev, b))
                return {"event": ev, "tickets": tickets, "match": how, "source": "mcp"}
        for term in surnames:
            found = [b for b in await _mcp_bookings(ev, term, errors)
                     if _same_person(booking_to_ticket(b), names)]
            if found:
                tickets = []
                for b in found:
                    tickets += booking_tickets(b, await _mcp_booking_detail(ev, b))
                return {"event": ev, "tickets": tickets, "match": "nome", "source": "mcp"}
    return {}


def _event_rank(e: dict, date_hint: str, event_id_hint: str, name_hint: str) -> tuple:
    """Prima la serata indicata sul biglietto: numero evento, poi data, poi nome."""
    name = _norm(e.get("name", ""))
    hint = _norm(name_hint)
    return (str(e.get("id")) != str(event_id_hint).strip(),
            event_day(e) != date_hint,
            not (hint and (hint in name or name in hint)),
            -int(e.get("startingTime") or 0))


async def find_request_tickets(emails: set[str], codes: set[str], names: set[str],
                               date_hint: str = "", event_id_hint: str = "",
                               name_hint: str = "") -> dict:
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

    events.sort(key=lambda e: _event_rank(e, date_hint, event_id_hint, name_hint))

    from mail import xceed_mcp
    errors: list[str] = []
    via_mcp: dict = {}
    if xceed_mcp.configured():
        try:
            via_mcp = await find_via_mcp(events, emails, codes, names, errors)
            if via_mcp and via_mcp["match"] != "nome":
                return via_mcp
        except Exception as e:
            logger.warning("Ricerca prenotazioni via MCP fallita: %s", e)
            errors.append(f"MCP: {str(e)[:100]}")
            via_mcp = {}

    weak: dict = via_mcp
    for ev in events:
        try:
            tickets = await event_tickets(ev)
        except Exception as e:
            logger.warning("Biglietti Xceed non leggibili per %s: %s", ev.get("name"), e)
            errors.append(f"Partner {ev.get('name', '')}: {str(e)[:100]}")
            continue
        found, how = match_tickets(tickets, emails, codes, names)
        if found and how != "nome":
            return {"event": ev, "tickets": found, "match": how}
        if found and not weak:
            weak = {"event": ev, "tickets": found, "match": how}
    if weak:
        return weak
    # Non trovati: si dice allo staff se la ricerca è stata completa o se qualcosa
    # ha dato errore (allora "non trovati" non vuol dire che non esistono).
    return {"errors": errors[:5], "events_checked": len(events)} if errors else {}


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
        amount, currency = p.get("amount"), p.get("currency", "")
        if isinstance(amount, dict):        # formato MCP: {"onlinePrice": 15, "currency": "EUR"}
            currency = amount.get("currency") or currency
            amount = amount.get("onlinePrice") or amount.get("amount")
        if amount not in (None, ""):
            price = f"€{amount}" if currency in ("EUR", "") else f"{amount} {currency}"
    plan = refund_plan(tickets)
    return {"count": len(tickets), "used": len(used), "inactive": len(inactive),
            "holders": holders, "booking_ids": booking_ids, "price": price,
            "offer": (tickets[0].get("offer") or {}).get("name", "") if tickets else "",
            "refund_plan": plan, "to_refund": sum(p["tickets"] for p in plan),
            "partial": any(not p["full"] for p in plan),
            "missing_codes": sum(p.get("missing_codes", 0) for p in plan)}


def refund_plan(tickets: list[dict]) -> list[dict]:
    """Cosa rimborsare, prenotazione per prenotazione: solo i biglietti NON usati e validi.

    - nessun biglietto della prenotazione è entrato → rimborso totale della prenotazione;
    - qualcuno è entrato (o è già rimborsato) → rimborso parziale, biglietto per
      biglietto, coi codici di quelli non usati: chi è entrato non si rimborsa mai;
    - tutti entrati → la prenotazione non compare.
    """
    by_booking: dict[str, list[dict]] = {}
    for t in tickets:
        bid = (t.get("booking") or {}).get("bookingId")
        if bid:
            by_booking.setdefault(str(bid), []).append(t)
    plan = []
    for bid, ts in sorted(by_booking.items()):
        todo = [t for t in ts if not t.get("hasCheckedIn") and t.get("isActive") is not False]
        if not todo:
            continue
        if len(todo) == len(ts):
            plan.append({"booking_id": bid, "full": True, "codes": [], "tickets": len(todo)})
            continue
        codes = [str(t["qrCode"]) for t in todo if t.get("qrCode")]
        plan.append({"booking_id": bid, "full": False, "codes": codes, "tickets": len(todo),
                     "missing_codes": len(todo) - len(codes)})
    return plan


# --- diagnosi (/debug/refunds?probe=...) -------------------------------------------

_PII = ("email", "name", "phone", "firstname", "lastname", "fullname", "address")


def mask(obj, depth: int = 0):
    """Struttura della risposta con i dati personali oscurati: serve a capire il formato."""
    if depth > 6:
        return "…"
    if isinstance(obj, dict):
        return {k: ("***" if any(p in k.lower() for p in _PII) and isinstance(v, str)
                    else mask(v, depth + 1)) for k, v in list(obj.items())[:40]}
    if isinstance(obj, list):
        return [mask(v, depth + 1) for v in obj[:3]] + ([f"... +{len(obj) - 3}"] if len(obj) > 3 else [])
    if isinstance(obj, str) and "@" in obj:
        return "***"
    return obj


async def probe(term: str) -> dict:
    """Cosa vedono Partner API e MCP per una ricerca (email, ordine o nome)."""
    from mail import xceed_mcp
    out: dict = {"term": "***" if "@" in term else term}
    try:
        events = await recent_events(settings.refund_window_days + 4)
        out["partner_events"] = [{"name": e.get("name"), "day": event_day(e), "id": e.get("id"),
                                  "uuid": e.get("uuid")} for e in events[:10]]
    except Exception as e:
        out["partner_events"] = f"errore: {str(e)[:200]}"
        events = []
    if events:
        try:
            out["partner_tickets_primo_evento"] = len(await event_tickets(events[0]))
        except Exception as e:
            out["partner_tickets_primo_evento"] = f"errore: {str(e)[:200]}"
    if xceed_mcp.configured():
        try:
            eps = await xceed_mcp.tool_json("list_endpoints", {"search": "refund"})
            out["endpoint_rimborso"] = mask(eps) if not isinstance(eps, str) else eps[:3000]
        except Exception as e:
            out["endpoint_rimborso"] = f"errore: {str(e)[:200]}"
        out["mcp"] = []
        for ev in events[:4]:
            row = {"event": ev.get("name"), "day": event_day(ev)}
            for form in ("uuid", "id"):
                if not ev.get(form):
                    continue
                try:
                    data = await xceed_mcp.tool_json("bookings_search", {
                        "eventId": str(ev[form]), "search": term, "limit": 3})
                    row[form] = mask(data)
                    found = _as_list(data)
                    row[f"{form}_estratti"] = [mask(booking_to_ticket(b)) for b in found]
                    # Dettaglio completo della prima prenotazione: serve a capire quale
                    # ID vuole booking_refund e se la prenotazione è rimborsabile.
                    if found:
                        b = found[0]
                        for key in ("id", "uuid", "legacyId"):
                            if not b.get(key):
                                continue
                            try:
                                row[f"booking_get_{key}"] = mask(await xceed_mcp.tool_json(
                                    "booking_get", {"eventId": str(ev[form]), "bookingId": str(b[key])}))
                                break
                            except Exception as e:
                                row[f"booking_get_{key}"] = f"errore: {str(e)[:200]}"
                except Exception as e:
                    row[form] = f"errore: {str(e)[:200]}"
            out["mcp"].append(row)
    else:
        out["mcp"] = "non configurato"
    return out
