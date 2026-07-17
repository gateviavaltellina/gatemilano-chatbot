"""Shared RAG context builder for WhatsApp and Instagram webhooks."""
from __future__ import annotations
import logging

from rag.event_store import (
    get_upcoming_events_compact,
    get_next_events_compact,
    get_events_for_date,
    get_events_for_month_compact,
    get_vip_candidates,
    find_event_dates_by_name,
)
from rag.date_utils import extract_query_dates, extract_query_months
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
# Alias con cui l'utente nomina l'ALTRA sede. Servono a iniettare la KB dell'altra
# sede anche per domande GENERALI (senza evento/data), es. "info su gate sardinia,
# età minima e prezzo" sul canale Milano. Per Milano NON includiamo "milano" da solo
# (troppo ambiguo: "vengo da Milano"): serve il brand esplicito.
_OTHER_VENUE_ALIASES = {
    "gate_sardinia": ("gate sardinia", "gate sardegna", "gate sardina", "gatesardinia",
                      "sardinia", "sardegna", "sardina"),
    "gate_milano": ("gate milano", "gatemilano", "gate valtellina", "valtellina"),
}
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

    # Testo degli ultimi 6 messaggi (user + bot) — serve sia per il topic VIP sia per
    # ripescare l'evento di cui si sta parlando quando il follow-up non lo nomina.
    history_text = " ".join(
        m.get("content", "") for m in (history or [])[-6:]
    ).lower()
    # Finestra più ampia solo per RIAGGANCIARE l'evento (anche dell'altra sede) nei
    # follow-up: un "e c'è dress code?" può arrivare vari turni dopo che l'evento è
    # stato nominato. Senza questa, il bot perde il contesto cross-venue e mescola le
    # policy delle due sedi (caso reale: dress code chiesto sul canale Milano per una
    # serata di Gate Sardinia).
    history_text_wide = " ".join(
        m.get("content", "") for m in (history or [])[-12:]
    ).lower()
    # L'utente nomina esplicitamente l'ALTRA sede (nel messaggio o nella chat recente)?
    # Se sì, iniettiamo la sua KB anche senza un evento specifico, così il bot può
    # rispondere a domande generali su quella sede (età, prezzi, dress code, contatti)
    # invece di rimandare al sito. Caso reale: "info su gate sardinia, età minima e
    # prezzo" arrivato sul canale Milano.
    _other_aliases = _OTHER_VENUE_ALIASES.get(other_venue, ())
    other_venue_mentioned = any(a in lower_text for a in _other_aliases) or \
        any(a in history_text for a in _other_aliases)

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
    # Follow-up che NON nomina l'evento ma ne eredita il topic dalla chat: recupera
    # l'evento dai messaggi recenti (caso reale: turno prima "stasera Perreo XL a Gate
    # Sardinia", poi "quanto costa l'ingresso?" — senza questo il bot perde l'evento e
    # risponde "non ho i dettagli sui prezzi" pur avendoli in contesto un attimo prima).
    if not name_dates and history_text_wide:
        name_dates = (find_event_dates_by_name(venue, history_text_wide)
                      or find_event_dates_by_name(other_venue, history_text_wide))
    # Le date risolte per nome vengono PRIMA: sono la data esatta in cui l'evento è
    # archiviato, mentre una data relativa ("questo sabato") è solo l'approssimazione
    # dell'utente e può cadere su un giorno adiacente. query_dates[0] guida anche il
    # lookup tavoli VIP, che va fatto sul giorno giusto.
    query_dates = list(dict.fromkeys(name_dates + explicit_dates))

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
    empty_dates = []
    for date_str in query_dates:
        day_events = get_events_for_date(venue, date_str)
        if day_events:
            date_parts.append(day_events)
        other_events = get_events_for_date(other_venue, date_str)
        if other_events:
            date_parts.append(f"[EVENTI A {other_venue_name.upper()} — venue diversa]\n{other_events}")
            cross_venue = True
        if not day_events and not other_events:
            empty_dates.append(date_str)

    # 2-bis. Mese citato per nome senza un giorno preciso ("e ad agosto?"): mostra gli
    # eventi di quel mese. Senza questo il bot vede solo la finestra breve e nega eventi
    # che sono in Sanity (caso reale: 30 eventi ad agosto ma rispondeva "non ho eventi
    # per agosto"). Salta i mesi per cui c'è già un giorno specifico tra query_dates.
    for (yr, mo) in extract_query_months(text):
        if any(d[:7] == f"{yr:04d}-{mo:02d}" for d in query_dates):
            continue
        mev = get_events_for_month_compact(venue, yr, mo)
        if mev:
            date_parts.append(mev)
        other_mev = get_events_for_month_compact(other_venue, yr, mo)
        if other_mev:
            date_parts.append(f"[EVENTI A {other_venue_name.upper()} — venue diversa]\n{other_mev}")
            cross_venue = True

    # STATO DI STASERA sempre nel contesto: il locale è aperto SOLO se c'è un evento in
    # calendario per il giorno di servizio corrente. Senza questo, a una domanda sugli
    # orari che non nomina "stasera" (es. "a che ora inizia a cantare?") il bot assumeva
    # di essere aperto anche in una sera CHIUSA (caso reale: "la serata è in corso
    # 22:00-03:00" mentre stasera era chiuso). Lo iniettiamo solo se l'utente non ha già
    # chiesto esplicitamente di oggi (in quel caso c'è già la nota "NESSUN EVENTO").
    from rag.date_utils import business_now  # import a runtime: rispetta il patch nei test
    today_str = business_now().strftime("%Y-%m-%d")
    if today_str not in query_dates:
        if get_events_for_date(venue, today_str):
            date_parts.insert(0, f"STATO DI STASERA ({today_str}): c'è un evento in programma → locale APERTO.")
        else:
            date_parts.insert(0, (
                f"STATO DI STASERA ({today_str}): NESSUN evento in programma → il locale è CHIUSO stasera. "
                f"Se il cliente chiede se siete aperti / cosa c'è stasera / a che ora si inizia o si chiude "
                f"stasera, di' che stasera il locale è CHIUSO; NON dire che 'la serata è in corso' né dare "
                f"orari di apertura come se fosse aperto. Indica il PROSSIMO evento in programma con la sua data."
            ))

    # Data richiesta (es. "oggi"/"stasera") SENZA eventi: dichiaralo esplicitamente,
    # altrimenti il bot pesca il primo della lista "PROSSIMI EVENTI" e lo spaccia per
    # stasera (allucinazione reale 8/7: "stasera c'è Flaco G" mentre Flaco G è il 9/7).
    if empty_dates:
        dates_str = ", ".join(empty_dates)
        venue_pretty = venue.replace("_", " ").title()
        date_parts.append(
            f"NESSUN EVENTO risulta in programma per la/e data/e richiesta/e ({dates_str}) a {venue_pretty}. "
            f"NON presentare come 'di oggi/stasera' un evento con una data diversa: se il cliente chiede di "
            f"oggi/stasera e per quella data non c'è nulla, dillo chiaramente. Puoi indicare qual è il PROSSIMO "
            f"evento citando la SUA data reale (dalla lista qui sotto), senza mai chiamarlo 'di stasera'."
        )

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
    if cross_venue or other_venue_mentioned:
        from rag.knowledge_cache import get as _get_kb
        other_kb = _get_kb(other_venue)
        if other_kb:
            parts.append(
                f"[INFO E POLICY {other_venue_name.upper()} — venue diversa: USA QUESTE "
                f"per rispondere alle domande su {other_venue_name} (anche senza un evento "
                f"specifico: età, dress code, rimborsi, tavoli, contatti, prezzi, SITO e "
                f"SOCIAL). Per rimandare a sito/social/email usa quelli di {other_venue_name} "
                f"(NON quelli di {venue.replace('_', ' ').title()}). NON applicare a "
                f"{venue.replace('_', ' ').title()} le policy di {other_venue_name}]\n{other_kb}"
            )
    return "\n\n---\n\n".join(parts), query_dates
