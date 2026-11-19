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
    event_name_match_score,
    has_active_event,
    get_events_around_compact,
    get_calendar_horizon,
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


def _explicit_venue(text: str) -> str | None:
    mentioned = [venue for venue, aliases in _OTHER_VENUE_ALIASES.items()
                 if any(alias in text.lower() for alias in aliases)]
    return mentioned[0] if len(mentioned) == 1 else None


def _named_topic(venue: str, text: str) -> tuple[list[str], str]:
    requested = _explicit_venue(text)
    target = requested or venue
    dates = find_event_dates_by_name(target, text)
    if not dates and not requested:
        other = _OTHER_VENUE[target]
        other_dates = find_event_dates_by_name(other, text)
        if other_dates:
            return other_dates, other
    return dates, target


def _history_topic(venue: str, history: list[dict]) -> tuple[str, list[str], str]:
    """Use the latest customer selection, not every date/name ever mentioned.

    Assistant suggestions are a fallback only. Relative dates in old messages
    cannot be reinterpreted as if they had been sent today.
    """
    recent = history[-12:]
    for role in ("user", "assistant"):
        for index in range(len(recent) - 1, -1, -1):
            message = recent[index]
            if message.get("role") != role:
                continue
            text = message.get("content", "")
            dates = extract_query_dates(text, explicit_only=True)
            named, target = _named_topic(venue, text)
            if role == "user" and not dates and extract_query_dates(text):
                # A historical "tomorrow" was resolved when the bot replied.
                # Reuse that concrete date, not today's interpretation and not
                # the older selection preceding this customer's change.
                for reply in recent[index + 1:]:
                    if reply.get("role") != "assistant":
                        continue
                    reply_text = reply.get("content", "")
                    reply_dates = extract_query_dates(reply_text, explicit_only=True)
                    if len(reply_dates) == 1:
                        _, reply_venue = _named_topic(target, reply_text)
                        return reply_text, reply_dates, reply_venue
            if dates or named or _explicit_venue(text):
                # "Anzi il 28 novembre" retains the previous event name for the
                # table ranking, but never the old date.
                topic = text
                if dates and not named:
                    for older in reversed(recent[:index]):
                        if older.get("role") != "user":
                            continue
                        old_text = older.get("content", "")
                        old_dates, old_target = _named_topic(target, old_text)
                        if old_dates:
                            topic += " " + old_text
                            if not _explicit_venue(text):
                                target = old_target
                            break
                return topic, dates or named, target
    return "", [], venue


async def _vip_lookup(venue: str, date_str: str | None, channel: str,
                      text: str = "", history_text: str = "") -> str:
    """Disponibilità tavoli per una venue, per la data richiesta (o i prossimi eventi).

    Una serata ha quasi sempre PIÙ eventi (Main Room + Club Room, o un off-site in
    contemporanea) e ognuno ha i SUOI tavoli. Restituirne uno solo ha prodotto due
    danni reali:

    - 12/9: il primo candidato del 19/9 era il Carl Cox al Carroponte (10 tavoli,
      ZERO link di acquisto) e il Perreo XL con 30 tavoli e link validi non veniva
      mai interrogato: il bot mandava il cliente a scrivere un'email.
    - 23/9: per il 2/10 il contesto conteneva solo i tavoli del Mantikore, e il bot
      ha DEDOTTO dall'assenza che il Raw non avesse tavoli ("non risultano tavoli VIP
      a listino online") — mentre il Raw aveva 4 Balcony liberi a €500 con link.

    Quindi: blocco COMPLETO per l'evento più pertinente (quello nominato dal cliente,
    altrimenti uno con tavoli acquistabili) e una RIGA DI RIEPILOGO per ogni altro
    evento della stessa data, così il bot sa che esistono e non può negarli.
    """
    found: list[tuple[str, str]] = []  # (nome evento, blocco)
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
            found.append((name, result))
    if not found:
        return ""

    match_text = text
    if not any(event_name_match_score(text, f[0]) for f in found):
        match_text = history_text
    main = max(found, key=lambda f: event_name_match_score(match_text, f[0]))
    if not event_name_match_score(match_text, main[0]):
        main = next((f for f in found if "Prenota: http" in f[1]
                     or "PRENOTA E PAGA ONLINE" in f[1]), found[0])

    others = [f for f in found if f is not main]
    if not others:
        return main[1]

    heading = "ALTRE SERATE DELLA STESSA DATA" if date_str else "ALTRE SERATE IN CALENDARIO"
    lines = [main[1], "", f"{heading} — link e disponibilità separati:"]
    for name, block in others:
        libere = block.count("→ Prenota: http")
        if libere:
            lines.append(
                f"- {name}: {libere} tavoli acquistabili online."
            )
        else:
            # Zero checkout individuali non significa zero disponibilità:
            # Revolut vende sulla mappa; un errore API non equivale a sold out.
            status = next((l for l in block.splitlines() if l.startswith("TAVOLI VIP")), "")
            lines.append(f"- {name}: {status}")
        lines.extend(f"  {line}" for line in block.splitlines()
                     if line.startswith("MAPPA TAVOLI 3D") or "PRENOTA E PAGA ONLINE" in line)
    lines.append(
        "⚠️ NON dire MAI che una di queste serate è senza tavoli o che i tavoli non "
        "sono a listino solo perché qui c'è un riepilogo. MANDA SUBITO il link della "
        "serata richiesta, con la sua data; non chiedere conferma per inviarlo. "
        "Non trasferire prezzi o disponibilità tra eventi diversi."
    )
    return "\n".join(lines)


async def build_rag_context(venue: str, text: str, history: list[dict] | None = None) -> tuple[str, list[str]]:
    """
    Build RAG context for a user message.
    Returns (rag_context_string, query_dates_list).

    Compact upcoming events (1 line each) keep context lean for tourists
    planning ahead; full event details are only injected for explicitly queried dates.
    """
    if venue == "milano_school_festival":
        from rag.school_festival import build_context
        return build_context(), []
    lower_text = text.lower()
    other_venue = _OTHER_VENUE.get(venue, "gate_milano")
    other_venue_name = _OTHER_VENUE_NAME.get(venue, "Gate Milano")

    # Testo degli ultimi 6 messaggi (user + bot) — serve sia per il topic VIP sia per
    # ripescare l'evento di cui si sta parlando quando il follow-up non lo nomina.
    history_text = " ".join(
        m.get("content", "") for m in (history or [])[-6:]
    ).lower()
    history_topic, history_dates, history_venue = _history_topic(venue, history or [])
    requested_venue = _explicit_venue(text)
    name_dates, target_venue = _named_topic(requested_venue or history_venue, text)
    # Keep venue selection even if another branch has an event on the same day.
    other_venue_mentioned = target_venue == other_venue
    explicit_dates = extract_query_dates(text)
    # Current date/event beats history. Relative terms in old messages are ignored.
    strict_dates = extract_query_dates(text, explicit_only=True)
    if strict_dates:
        query_dates = strict_dates
    elif name_dates:
        # Preserve the named-event fallback for approximate relative dates.
        query_dates = list(dict.fromkeys(name_dates + explicit_dates))
    elif explicit_dates:
        query_dates = explicit_dates
    else:
        query_dates = history_dates

    # 1. VIP context — when VIP keywords in current message OR recent history.
    vip_context = ""
    if any(t in lower_text for t in _VIP_TRIGGERS) or any(t in history_text for t in _VIP_TRIGGERS):
        lookup_date = query_dates[0] if query_dates else None
        vip_context = await _vip_lookup(target_venue, lookup_date,
                                        _VENUE_CHANNEL[target_venue], text, history_topic)
        # An unqualified date can refer to the other branch, but never fall back
        # merely because the selected event has no tables or its API failed.
        if (not vip_context and lookup_date and not requested_venue and not name_dates
                and not get_events_for_date(target_venue, lookup_date)):
            fallback_venue = _OTHER_VENUE[target_venue]
            fallback = await _vip_lookup(fallback_venue, lookup_date,
                                         _VENUE_CHANNEL[fallback_venue], text, history_topic)
            if fallback:
                vip_context, target_venue = fallback, fallback_venue
        if vip_context and target_venue == other_venue:
            vip_context = f"[TAVOLI A {other_venue_name.upper()} — venue diversa]\n{vip_context}"
            other_venue_mentioned = True

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
        # Salta il mese solo se il cliente ha scritto ESPLICITAMENTE un giorno di quel
        # mese: una data dedotta dal nome di un evento non basta. Caso reale (IG 13/9):
        # la parola "Milan" agganciava un evento del 31/10, e quella data spuria faceva
        # sparire tutto l'elenco di ottobre — il bot rispondeva che a metà ottobre non
        # c'era nulla mentre il 16/10 e il 17/10 erano pieni.
        if any(d[:7] == f"{yr:04d}-{mo:02d}" for d in explicit_dates):
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
        # NB: has_active_event ignora gli eventi ANNULLATI — una serata annullata non
        # tiene aperto il locale (il suo documento resta però leggibile per dirlo).
        if has_active_event(venue, today_str):
            date_parts.insert(0, f"STATO DI STASERA ({today_str}): c'è un evento in programma → locale APERTO.")
        else:
            date_parts.insert(0, (
                f"STATO DI STASERA ({today_str}): NESSUN evento in programma → il locale è CHIUSO stasera. "
                f"Se il cliente chiede se siete aperti / cosa c'è stasera / a che ora si inizia o si chiude "
                f"stasera, di' che stasera il locale è CHIUSO; NON dire che 'la serata è in corso' né dare "
                f"orari di apertura come se fosse aperto. Indica il PROSSIMO evento in programma con la sua data."
            ))
            # Cross-venue: questa sede è chiusa ma l'ALTRA stasera è APERTA → il bot
            # deve dirlo subito (caso reale, canale Milano d'estate: "possiamo prenotare
            # due biglietti?" → "chiuso" secco, senza citare la serata in corso a
            # Gate Sardinia — che è quasi sempre ciò che il cliente intende).
            if has_active_event(other_venue, today_str):
                other_tonight = get_events_for_date(other_venue, today_str)
                if other_tonight:
                    date_parts.insert(1, (
                        f"PERÒ STASERA {other_venue_name.upper()} È APERTO [venue diversa]. "
                        f"Quando dici che questa sede è chiusa, aggiungi SUBITO che stasera "
                        f"{other_venue_name} è aperto con la serata qui sotto e proponila "
                        f"(molti scrivono a questo numero intendendo l'altra sede):\n{other_tonight}"
                    ))
                    cross_venue = True

    # Data richiesta (es. "oggi"/"stasera") SENZA eventi: dichiaralo esplicitamente,
    # altrimenti il bot pesca il primo della lista "PROSSIMI EVENTI" e lo spaccia per
    # stasera (allucinazione reale 8/7: "stasera c'è Flaco G" mentre Flaco G è il 9/7).
    if empty_dates:
        venue_pretty = venue.replace("_", " ").title()
        # Le date PASSATE vanno trattate diversamente: gli eventi passati non restano
        # in archivio, quindi "nessun evento in store" NON significa "quella sera non
        # c'era nulla". Caso reale: "chi era il fotografo della serata dell'1/8?" →
        # il bot negava la serata ("non risulta in programma da noi") appena passata.
        past_dates = [d for d in empty_dates if d < today_str]
        future_dates = [d for d in empty_dates if d >= today_str]
        if future_dates:
            dates_str = ", ".join(future_dates)
            date_parts.append(
                f"NESSUN EVENTO risulta in programma per la/e data/e richiesta/e ({dates_str}) a {venue_pretty}. "
                f"NON presentare come 'di oggi/stasera' un evento con una data diversa: se il cliente chiede di "
                f"oggi/stasera e per quella data non c'è nulla, dillo chiaramente. Puoi indicare qual è il PROSSIMO "
                f"evento citando la SUA data reale (dalla lista qui sotto), senza mai chiamarlo 'di stasera'."
            )
            # Le serate VICINE alla data richiesta: senza queste il bot vede solo la
            # finestra breve e nega serate che esistono (caso reale: compleanno il
            # 17/11, weekend 14-15/11 pieno, risposta "non ho eventi").
            for d in future_dates:
                around = get_events_around_compact(venue, d)
                if around:
                    date_parts.append(around)
        if past_dates:
            dates_str = ", ".join(past_dates)
            date_parts.append(
                f"NOTA DATE PASSATE: la/e data/e richiesta/e ({dates_str}) è/sono GIÀ PASSATE e le serate "
                f"passate non restano in archivio. NON dire MAI che quella sera 'non c'era nulla in programma' "
                f"o 'non risulta una serata da noi': con ogni probabilità la serata c'è stata. Rispondi alla "
                f"domanda (foto/video, fotografo, oggetti smarriti, rimborsi...) dando per assodato che la "
                f"serata si sia svolta e indirizza al canale giusto per quel tema."
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
    # Orizzonte reale del calendario: impedisce al bot di dedurre dalla lista (parziale)
    # che la programmazione finisca prima di quanto sia vero. Caso reale: "la
    # programmazione arriva fino a metà settembre" con eventi confermati fino a dicembre.
    horizon = get_calendar_horizon(venue)

    focus = ""
    if history and query_dates:
        focus = (
            f"DATE DI RIFERIMENTO DELLA RICHIESTA ATTUALE: {', '.join(query_dates)}. "
            f"Sede richiesta: {target_venue.replace('_', ' ').title()}. "
            "Questa selezione segue l'ultimo messaggio del cliente e prevale sulle "
            "vecchie date o serate nella chat: NON riproporre la data precedente "
            "e NON chiedere di riconfermarla. Usa i link e i prezzi della selezione "
            "attuale qui forniti. Se il cliente prosegue una richiesta tavoli "
            "cambiando data, continua con i tavoli e invia subito il link corretto."
        )
    parts = [p for p in [vip_context, *date_parts, upcoming, horizon, focus] if p]

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
