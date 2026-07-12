"""
Simple in-memory event store. No embedding, no vector DB.
Populated on startup by Sanity/Xceed sync, reset on each restart.
"""
from __future__ import annotations
import logging
import re
import unicodedata
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_ROME = ZoneInfo("Europe/Rome")
logger = logging.getLogger(__name__)


def _today_start_utc() -> int:
    """Midnight UTC del 'giorno di servizio' (rollover alle 06:00, vedi date_utils):
    tra mezzanotte e le 06:00 conta ancora il giorno precedente, così la serata in
    corso (eventi fino alle 05:00) non viene scambiata per passata.
    Matches how date_ts is stored (midnight UTC della data Rome)."""
    from rag.date_utils import business_now
    bnow = business_now()
    return int(datetime(bnow.year, bnow.month, bnow.day, tzinfo=timezone.utc).timestamp())

# venue_key → list of {"id": str, "document": str, "metadata": dict}
_store: dict[str, list[dict]] = {}


def _get(venue: str) -> list[dict]:
    return _store.setdefault(venue, [])


def _norm_name(name: str) -> str:
    """Normalizza un nome evento per il confronto cross-source (Sanity vs Xceed):
    minuscolo, accenti rimossi (via NFKD), apostrofi rimossi, resto non-alfanumerico
    → spazio. Così 'Don't Tell Mama' (apostrofo curvo) e \"DON'T TELL MAMA\"
    (apostrofo dritto) collassano sullo stesso valore."""
    s = unicodedata.normalize("NFKD", name or "").lower()
    s = re.sub(r"[’‘'`]", "", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return s.strip()


_DATE_MATCH_TOLERANCE = 86400  # ±1 giorno: assorbe differenze di orario tra fonti


def has_matching_event(venue: str, date_ts: int, name: str, exclude_source: str | None = None) -> bool:
    """True se esiste già un evento per lo stesso venue con data entro ±1 giorno e
    nome equivalente (uguale o con prefisso comune), proveniente da una fonte diversa
    da exclude_source.

    Serve a evitare doppioni quando più sync popolano lo stesso store: Sanity è la
    fonte primaria (dati più ricchi: sala, generi, sold-out, descrizioni, e copre
    anche biglietterie non-Xceed), Xceed Open API viene usato solo come fallback per
    eventi che Sanity non ha ancora."""
    target = _norm_name(name)
    if not target:
        return False
    for e in _get(venue):
        meta = e["metadata"]
        if meta.get("type") != "event":
            continue
        if exclude_source and meta.get("source") == exclude_source:
            continue
        if abs(meta.get("date_ts", 0) - date_ts) > _DATE_MATCH_TOLERANCE:
            continue
        existing = _norm_name(meta.get("event_name", ""))
        if existing and (existing == target or existing.startswith(target) or target.startswith(existing)):
            return True
    return False


def upsert_event(venue: str, event_id: str, document: str, metadata: dict):
    events = _get(venue)
    _store[venue] = [e for e in events if e["id"] != event_id]
    _store[venue].append({"id": event_id, "document": document, "metadata": metadata})


def delete_stale_events(venue: str, current_event_ids: list[str], source: str = None):
    current = set(current_event_ids)
    events = _get(venue)
    before = len(events)
    _store[venue] = [
        e for e in events
        if not (
            e["metadata"].get("type") == "event"
            and (source is None or e["metadata"].get("source") == source)
            and e["id"] not in current
        )
    ]
    removed = before - len(_store[venue])
    if removed:
        logger.info("Rimossi %d eventi stale da '%s'%s", removed, venue, f" ({source})" if source else "")


def get_upcoming_events(venue: str, days: int = 14) -> str:
    today_ts = _today_start_utc()
    end_ts = today_ts + days * 86400
    events = [
        e for e in _get(venue)
        if e["metadata"].get("type") == "event"
        and today_ts <= e["metadata"].get("date_ts", 0) <= end_ts
    ]
    events.sort(key=lambda e: e["metadata"].get("date_ts", 0))
    return "\n\n---\n\n".join(e["document"] for e in events)


def _compact_event_line(e: dict) -> str:
    """Rende un evento in una riga compatta '• data: nome · sala · prezzo/stato — link'.
    Condivisa da get_upcoming_events_compact e get_next_events_compact."""
    meta = e["metadata"]
    name = meta.get("event_name", "Evento")
    doc = e["document"]

    date_line = ""
    room = ""
    hours = ""
    min_price = ""
    sold_out = False
    selling_fast = False

    for line in doc.split("\n"):
        if line.startswith("Data:"):
            date_line = line.replace("Data:", "").strip()
        elif line.startswith("Sala:"):
            room = line.replace("Sala:", "").strip()
        elif line.startswith("Orari:"):
            # Orario specifico della serata (da Sanity): va tenuto anche nella lista
            # compatta, altrimenti una domanda sugli orari senza data esplicita non lo
            # vedrebbe (il compact è l'unico contesto iniettato) e il bot userebbe il default.
            hours = line.replace("Orari:", "").strip()
        elif "ESAURITI" in line:
            sold_out = True
        elif "Sold out velocemente" in line:
            selling_fast = True
        elif line.strip().startswith("•") and "€" in line:
            m = re.search(r"€(\d+)", line)
            if m and not min_price:
                min_price = m.group(1)

    parts = [name]
    if room:
        parts.append(room)
    if hours:
        parts.append(f"orari {hours}")
    if sold_out:
        parts.append("ESAURITI")
    elif selling_fast:
        parts.append("ultimi biglietti")
    elif min_price:
        parts.append(f"da €{min_price}")

    ticket = meta.get("ticket_url", "")
    ticket_str = f" — {ticket}" if ticket else ""
    return f"• {date_line}: {' · '.join(parts)}{ticket_str}"


def get_upcoming_events_compact(venue: str, days: int = 14) -> str:
    """1-line-per-event summary — lighter RAG context for upcoming events.
    Full details are injected separately only for dates the user explicitly asked about."""
    today_ts = _today_start_utc()
    end_ts = today_ts + days * 86400
    events = [
        e for e in _get(venue)
        if e["metadata"].get("type") == "event"
        and today_ts <= e["metadata"].get("date_ts", 0) <= end_ts
    ]
    if not events:
        return ""
    events.sort(key=lambda e: e["metadata"].get("date_ts", 0))
    venue_label = venue.replace("_", " ").title()
    lines = [f"PROSSIMI EVENTI {venue_label.upper()} (prossimi {days} giorni):"]
    lines.extend(_compact_event_line(e) for e in events)
    return "\n".join(lines)


def get_next_events_compact(venue: str, limit: int = 8, days: int = 220) -> str:
    """Lista compatta dei PROSSIMI `limit` eventi in calendario, IGNORANDO la finestra
    breve dei 14 giorni. Serve alle domande su stagione/riapertura/calendario: Gate
    Milano programma la stagione con mesi di anticipo, quindi i prossimi eventi possono
    cadere ben oltre i 14 giorni. Senza questo, a fine giugno/luglio il bot risponde
    'stagione non ancora annunciata' pur avendo gli eventi (set-dic) già in Sanity."""
    today_ts = _today_start_utc()
    end_ts = today_ts + days * 86400
    events = [
        e for e in _get(venue)
        if e["metadata"].get("type") == "event"
        and today_ts <= e["metadata"].get("date_ts", 0) <= end_ts
    ]
    if not events:
        return ""
    events.sort(key=lambda e: e["metadata"].get("date_ts", 0))
    events = events[:limit]
    venue_label = venue.replace("_", " ").title()
    lines = [
        f"PROSSIMI EVENTI IN CALENDARIO {venue_label.upper()} "
        f"(date confermate, in ordine cronologico):"
    ]
    lines.extend(_compact_event_line(e) for e in events)
    return "\n".join(lines)


def get_events_for_month_compact(venue: str, year: int, month: int, limit: int = 40) -> str:
    """Lista compatta (1 riga/evento) degli eventi di UN MESE, dai giorni non ancora
    passati in poi. Per le domande su un intero mese ("e ad agosto?"): mostra ciò che
    c'è davvero in calendario invece di far dire al bot "non ho eventi per quel mese"."""
    from calendar import monthrange
    start = int(datetime(year, month, 1, tzinfo=timezone.utc).timestamp())
    last_day = monthrange(year, month)[1]
    end = int(datetime(year, month, last_day, tzinfo=timezone.utc).timestamp()) + 86400
    lo = max(start, _today_start_utc())  # niente giorni già passati del mese corrente
    events = [
        e for e in _get(venue)
        if e["metadata"].get("type") == "event"
        and lo <= e["metadata"].get("date_ts", 0) < end
    ]
    if not events:
        return ""
    events.sort(key=lambda e: e["metadata"].get("date_ts", 0))
    events = events[:limit]
    venue_label = venue.replace("_", " ").title()
    month_label = datetime(year, month, 1).strftime("%B %Y")
    lines = [f"EVENTI {venue_label.upper()} — {month_label} (date confermate in calendario):"]
    lines.extend(_compact_event_line(e) for e in events)
    return "\n".join(lines)


def get_events_for_date(venue: str, date_str: str) -> str:
    day_start = int(datetime.strptime(date_str[:10], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp())
    day_end = day_start + 86400
    events = [
        e for e in _get(venue)
        if e["metadata"].get("type") == "event"
        and day_start <= e["metadata"].get("date_ts", 0) < day_end
    ]
    return "\n\n---\n\n".join(e["document"] for e in events)


# Parole troppo generiche per identificare un evento dal nome (evitano falsi match).
_NAME_STOPWORDS = {
    "gate", "milano", "sardinia", "sardegna", "budoni", "club", "live", "show",
    "night", "serata", "serate", "evento", "eventi", "party", "festival", "tour",
    "presents", "open", "opening", "closing",
}

# Nomi artista CORTI (<4 lettere, normalizzati: minuscolo, senza accenti) di forte
# richiamo che vanno SEMPRE risolti. Senza questa allowlist resterebbero sotto la
# soglia anti-rumore: es. "Guè" (token "gue") non verrebbe mai matchato. Aggiungere
# qui gli headliner costosi con nome corto man mano che entrano in cartellone.
_SHORT_ARTIST_ALLOWLIST = {"gue"}


def _name_tokens(s: str) -> set[str]:
    """Token significativi di un nome/lineup: lunghi >=4 lettere, più i nomi artista
    corti in allowlist (es. 'gue')."""
    return {t for t in _norm_name(s).split()
            if len(t) >= 4 or t in _SHORT_ARTIST_ALLOWLIST}


def _edit_distance(a: str, b: str, max_d: int) -> int:
    """Distanza di Levenshtein con early-exit: se supera max_d ritorna max_d+1."""
    if abs(len(a) - len(b)) > max_d:
        return max_d + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        row_best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            v = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(v)
            row_best = min(row_best, v)
        if row_best > max_d:
            return max_d + 1
        prev = cur
    return prev[-1]


def _has_name_match(query_tokens: set[str], name_tokens: set[str]) -> bool:
    """True se il messaggio identifica l'evento. Tre livelli, dal più sicuro:
    1) ESATTO (token identico);
    2) TRONCAMENTO/soprannome: token del messaggio (>=4) prefisso di un nome più lungo
       ('rondo' → 'rondodasosa', 'villa' → 'villabanks');
    3) FUZZY (typo): 1-2 lettere sbagliate su token lunghi e di lunghezza simile
       ('vilabanks' → 'villabanks', 'rondodasola' → 'rondodasosa').
    Le guardie (lunghezza minima, differenza di lunghezza <=2) evitano falsi match su
    parole comuni del messaggio."""
    if query_tokens & name_tokens:
        return True
    for qt in query_tokens:
        if len(qt) < 4:
            continue
        for nt in name_tokens:
            # 2) troncamento: qt è il prefisso di un nome più lungo
            if len(nt) >= len(qt) + 2 and nt.startswith(qt):
                return True
            # 3) fuzzy: solo token lunghi (>=5) e di lunghezza simile (±2)
            if len(qt) >= 5 and len(nt) >= 5 and abs(len(qt) - len(nt)) <= 2:
                max_d = 1 if min(len(qt), len(nt)) <= 7 else 2
                if _edit_distance(qt, nt, max_d) <= max_d:
                    return True
    return False


def find_event_dates_by_name(venue: str, text: str, days: int = 300, limit: int = 2) -> list[str]:
    """Risolve un evento dal nome/lineup citato nel messaggio (es. un artista) quando
    l'utente NON ha dato una data esplicita. Cerca su tutta la stagione (`days` giorni)
    gli eventi il cui titolo condivide almeno un token significativo (>=4 lettere) col
    messaggio, e ritorna fino a `limit` date YYYY-MM-DD in ordine cronologico.

    Serve a far trovare al bot eventi oltre la finestra "prossimi giorni" (es. chiedere
    di un artista che suona tra mesi) e ad alimentare il lookup tavoli per quella serata.
    Finestra ampia (300gg ~ 10 mesi) perché Gate Milano programma la stagione con largo
    anticipo: un cliente che chiede di un headliner a 4 mesi deve comunque trovarlo.
    """
    tokens = _name_tokens(text) - _NAME_STOPWORDS
    if not tokens:
        return []
    today_ts = _today_start_utc()
    end_ts = today_ts + days * 86400
    matched: list[tuple[int, str]] = []
    for e in _get(venue):
        m = e["metadata"]
        if m.get("type") != "event":
            continue
        ts = m.get("date_ts", 0)
        if not (today_ts <= ts <= end_ts):
            continue
        # Cerca su titolo + lineup: un artista può essere in `artists` (Sanity) ma
        # NON nel titolo (es. Sardinia 10/7: titolo "Davide T", in lineup anche
        # Kamelia/Dfifonte/Asci). Senza questo, chi chiede di quegli artisti non
        # troverebbe la serata.
        searchable = m.get("event_name", "")
        artists = m.get("artists")
        if artists:
            searchable += " " + " ".join(artists)
        name_tokens = _name_tokens(searchable)
        if _has_name_match(tokens, name_tokens):
            di = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")
            matched.append((ts, di))
    matched.sort(key=lambda x: x[0])
    out: list[str] = []
    for _, di in matched:
        if di not in out:
            out.append(di)
        if len(out) >= limit:
            break
    return out


def count(venue: str) -> int:
    return len([e for e in _get(venue) if e["metadata"].get("type") == "event"])


def get_ticket_url_for_date(venue: str, date_str: str) -> str:
    """Return the ticketUrl for the first event on date_str, or empty string."""
    day_start = int(datetime.strptime(date_str[:10], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp())
    day_end = day_start + 86400
    for e in _get(venue):
        meta = e["metadata"]
        if (meta.get("type") == "event"
                and day_start <= meta.get("date_ts", 0) < day_end
                and meta.get("ticket_url")):
            return meta["ticket_url"]
    return ""


def get_all_ticket_urls_for_date(venue: str, date_str: str) -> list[str]:
    """Return all ticketUrls for events on date_str (to try VIP lookup on each)."""
    day_start = int(datetime.strptime(date_str[:10], "%Y-%m-%d")
                    .replace(tzinfo=timezone.utc).timestamp())
    day_end = day_start + 86400
    urls = []
    for e in _get(venue):
        meta = e["metadata"]
        if (meta.get("type") == "event"
                and day_start <= meta.get("date_ts", 0) < day_end
                and meta.get("ticket_url")):
            urls.append(meta["ticket_url"])
    return urls


def get_vip_candidates(venue: str, date_str: str | None = None, days: int = 14) -> list[tuple[str, str, str, str]]:
    """Eventi candidati per il lookup tavoli VIP: lista di
    (event_name, date_iso, ticket_url, sanity_id).

    Se `date_str` è dato, solo gli eventi di quel giorno; altrimenti i prossimi `days`
    giorni in ordine di data. `date_iso` è YYYY-MM-DD (dalla data Rome salvata in date_ts),
    pronto per l'endpoint del sito che risolve l'evento per name+date. `sanity_id` serve
    alla Sardegna per costruire /tavoli?event=<id> e /api/vip/availability?event=<id>.
    """
    out: list[tuple[str, str, str, str]] = []
    if date_str:
        day_start = int(datetime.strptime(date_str[:10], "%Y-%m-%d")
                        .replace(tzinfo=timezone.utc).timestamp())
        day_end = day_start + 86400
        cand = [e for e in _get(venue)
                if e["metadata"].get("type") == "event"
                and day_start <= e["metadata"].get("date_ts", 0) < day_end]
        cand.sort(key=lambda e: e["metadata"].get("date_ts", 0))
        for e in cand:
            m = e["metadata"]
            out.append((m.get("event_name", ""), date_str[:10], m.get("ticket_url", ""), m.get("sanity_id", "")))
    else:
        today_ts = _today_start_utc()
        end_ts = today_ts + days * 86400
        cand = [e for e in _get(venue)
                if e["metadata"].get("type") == "event"
                and today_ts <= e["metadata"].get("date_ts", 0) <= end_ts]
        cand.sort(key=lambda e: e["metadata"].get("date_ts", 0))
        for e in cand:
            m = e["metadata"]
            di = datetime.fromtimestamp(m.get("date_ts", 0), tz=timezone.utc).strftime("%Y-%m-%d")
            out.append((m.get("event_name", ""), di, m.get("ticket_url", ""), m.get("sanity_id", "")))
    return out
