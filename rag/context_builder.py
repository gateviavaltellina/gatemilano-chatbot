"""Shared RAG context builder for WhatsApp and Instagram webhooks."""
from __future__ import annotations
import logging

from rag.event_store import (
    get_upcoming_events_compact,
    get_next_events_compact,
    get_events_for_date,
    get_vip_candidates,
    find_event_dates_by_name,
)
from rag.date_utils import extract_query_dates
from rag.vip_tables import get_vip_tables_context, get_vip_tables_via_site, get_vip_tables_sardinia

logger = logging.getLogger(__name__)

# Domande sulla STAGIONE / CALENDARIO / RIAPERTURA: non citano una data né un artista,
# quindi né la data esplicita né il match per nome scattano, e la finestra breve dei 14
# giorni può essere vuota (a fine giugno/luglio i primi eventi di set sono >14gg). Senza
# un segnale dedicato il bot risponde dalla sola KB statica ("stagione set–giu") →
# "date non ancora annunciate", pur avendo gli eventi già in Sanity. Caso reale Milano:
# "info sull'apertura della stagione invernale, date e orari?".
_SEASON_TRIGGERS = {
    "stagione", "prossima stagione", "riapertura", "riaprite", "riapre", "riapri",
    "quando aprite", "quando apre", "apertura", "calendario", "programmazione",
    "prossimi eventi", "eventi in programma", "che eventi", "quali eventi",
    "date della stagione", "date stagione", "line up", "line-up", "lineup",
    "in programma", "prossime serate", "prossime date",
}
_VIP_TRIGGERS = {
    "tavolo", "tavoli", "vip", "bottle", "bottiglia", "bottiglie", "minimo",
    "table", "tables", "backstage", "disponibil", "prenotare", "prenot",
    "zona f", "zona b", "zona c", "balcony", "console",
    "mappa", "piantina", "planimetria",
}
_OTHER_VENUE = {"gate_milano": "gate_sardinia", "gate_sardinia": "gate_milano"}
_OTHER_VENUE_NAME = {"gate_milano": "Gate Sardinia", "gate_sardinia": "Gate Milano"}
# Slug canale Xceed per i link di checkout dei tavoli VIP (per venue).
_VENUE_CHANNEL = {"gate_milano": "gate-milano", "gate_sardinia": "gate-sardinia"}


async def _vip_lookup(venue: str, date_str: str | None, channel: str) -> str:
    """Disponibilità tavoli per una venue. Candidati: eventi della data richiesta,
    oppure i prossimi in programma (in ordine); si ferma al primo che ha tavoli.
    Milano usa l'endpoint del sito (name+date); Sardegna l'endpoint /api/vip/availability
    (MAI link Xceed); le altre venue la pipeline Xceed. Ritorna "" se nessun tavolo."""
    for name, date_iso, ticket_url, sanity_id in get_vip_candidates(venue, date_str):
        if venue == "gate_milano":
            result = await get_vip_tables_via_site(name, date_iso)
        elif venue == "gate_sardinia":
            result = await get_vip_tables_sardinia(sanity_id)
        else:
            if "xceed" not in (ticket_url or ""):
                continue
            result = await get_vip_tables_context(ticket_url, channel)
        if result:
            return result
    return ""


async def build_rag_context(venue: str, text: str, history: list[dict] | None = None) -> tuple[str, list[str]]:
    """
    Build RAG context for a user message.
    Returns (rag_context_string, query_dates_list).

    Compact upcoming events (1 line each) keep context lean for tourists
    planning ahead; full event details are only injected for explicitly queried dates.
    """
    lower_text = text.lower()
    other_venue = _OTHER_VENUE.get(venue, "gate_milano")
    other_venue_name = _OTHER_VENUE_NAME.get(venue, "Gate Milano")
    channel = _VENUE_CHANNEL.get(venue, "gate-milano")

    explicit_dates = extract_query_dates(text)
    # Risolvi SEMPRE anche l'evento citato per nome/artista, non solo come fallback
    # quando manca la data. Caso reale (Perreo XL "questo sabato"): l'utente dà una
    # data relativa che cade sul giorno X, ma l'evento in Sanity è indicizzato su un
    # giorno adiacente (orario di inizio a cavallo di mezzanotte → date_ts spostato di
    # un giorno, vedi sanity_sync._build_document). Con la sola data non troveremmo
    # l'evento e il bot risponderebbe "non ho i dettagli" pur avendone in Sanity.
    name_dates = find_event_dates_by_name(venue, text)
    # Cross-venue per nome: se l'artista/evento non è di QUESTA venue, prova l'ALTRA
    # (caso reale: "Guè"/"Melons"/"Rondodasosa" citati sul canale Milano ma in
    # cartellone a Gate Sardinia). Così scattano evento e tavoli dell'altra location.
    if not name_dates:
        name_dates = find_event_dates_by_name(other_venue, text)
    # Le date risolte per nome vengono PRIMA: sono la data esatta in cui l'evento è
    # archiviato, mentre una data relativa ("questo sabato") è solo l'approssimazione
    # dell'utente e può cadere su un giorno adiacente. query_dates[0] guida anche il
    # lookup tavoli VIP, che va fatto sul giorno giusto.
    query_dates = list(dict.fromkeys(name_dates + explicit_dates))

    # Check history for VIP topic — last 6 messages (3 turns)
    history_text = " ".join(
        m.get("content", "") for m in (history or [])[-6:]
    ).lower()

    # 1. VIP context — when VIP keywords in current message OR recent history.
    vip_context = ""
    if any(t in lower_text for t in _VIP_TRIGGERS) or any(t in history_text for t in _VIP_TRIGGERS):
        vip_context = await _vip_lookup(venue, query_dates[0] if query_dates else None, channel)
        # Cross-venue: il cliente scrive a una venue ma chiede di un evento dell'ALTRA
        # (caso reale: "tavoli del 5 luglio a Gate Sardinia" sul numero di Milano). Se
        # qui non troviamo tavoli e c'è una data richiesta con un evento nell'altra
        # venue, prendiamo i SUOI tavoli ETICHETTANDO la provenienza, così il bot
        # risponde con i dati giusti senza spacciarli per questa venue.
        if not vip_context and query_dates:
            other_channel = _VENUE_CHANNEL.get(other_venue, "gate-milano")
            other_vip = await _vip_lookup(other_venue, query_dates[0], other_channel)
            if other_vip:
                vip_context = f"[TAVOLI A {other_venue_name.upper()} — venue diversa]\n{other_vip}"

    # 2. Full event details for specifically queried dates
    date_parts = []
    cross_venue = False
    for date_str in query_dates:
        day_events = get_events_for_date(venue, date_str)
        if day_events:
            date_parts.append(day_events)
        other_events = get_events_for_date(other_venue, date_str)
        if other_events:
            date_parts.append(f"[EVENTI A {other_venue_name.upper()} — venue diversa]\n{other_events}")
            cross_venue = True

    # 3. Compact upcoming list (title + date + link, 1 line per event, 14 giorni).
    # Se il cliente chiede della stagione/calendario/riapertura, mostra i PROSSIMI
    # eventi in calendario anche oltre i 14 giorni (Milano programma con mesi di
    # anticipo): così il bot NON dice "stagione non ancora annunciata" avendo gli
    # eventi in Sanity.
    if any(t in lower_text for t in _SEASON_TRIGGERS):
        upcoming = get_next_events_compact(venue) or get_upcoming_events_compact(venue, days=14)
    else:
        upcoming = get_upcoming_events_compact(venue, days=14)

    # NB: la knowledge base statica NON è più qui — è costante per venue e viene
    # iniettata nel blocco system cacheato (vedi ai/claude_client.build_system_blocks).
    # Qui resta solo il contesto DINAMICO (cambia per messaggio/giorno).
    parts = [p for p in [vip_context, *date_parts, upcoming] if p]

    # 4. Cross-venue: se c'è un evento dell'ALTRA venue, inietta anche le SUE info e
    # policy (età, dress code, rimborsi, tavoli…). Senza questo il bot risolve l'evento
    # ma non può rispondere alle domande di policy su quella location (caso reale:
    # "minorenni al concerto di Massimo Pericolo a Gate Sardinia?") e rimanda al sito
    # pur conoscendo la risposta. La KB dell'altra venue non è nel prompt del canale.
    if cross_venue:
        from rag.knowledge_cache import get as _get_kb
        other_kb = _get_kb(other_venue)
        if other_kb:
            parts.append(
                f"[INFO E POLICY {other_venue_name.upper()} — venue diversa: USA QUESTE "
                f"per rispondere alle domande su quell'evento (età, dress code, rimborsi, "
                f"tavoli, contatti). NON applicarle a {venue.replace('_', ' ').title()}]\n{other_kb}"
            )
    return "\n\n---\n\n".join(parts), query_dates
