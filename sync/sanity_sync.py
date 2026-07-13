import re
import json
import httpx
import logging
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from rag.event_store import upsert_event, delete_stale_events
from rag.date_utils import CLUB_NIGHT_ROLLOVER_HOUR

_ROME = ZoneInfo("Europe/Rome")


def _service_day(dt_rome: datetime):
    """Giorno 'di servizio' del club per un istante già in fuso Rome: una serata che
    inizia dopo mezzanotte appartiene ancora alla NOTTE precedente, quindi applichiamo
    lo stesso rollover di date_utils.business_now (−6h).

    Necessario perché Sanity Sardegna salva le date come '<giorno>T22:00:00Z', cioè
    00:00 Rome del GIORNO DOPO: senza rollover l'evento del 4 luglio finirebbe
    indicizzato (e mostrato) come 5 luglio, e 'questo sabato' non lo troverebbe."""
    return (dt_rome - timedelta(hours=CLUB_NIGHT_ROLLOVER_HOUR)).date()

logger = logging.getLogger(__name__)

# Telemetria ultimo sync per venue (esposta su /debug/events): permette di
# diagnosticare da browser PERCHÉ una venue è senza eventi, senza leggere i log.
_last_sync: dict[str, dict] = {}


def get_last_sync_status() -> dict[str, dict]:
    return _last_sync

# v2023-08-01+ serve per il parametro `perspective` (lettura bozze); le GROQ usate
# sono basilari e identiche tra versioni.
SANITY_API_VERSION = "2023-08-01"

SANITY_PROJECTS = {
    "gate_milano": {
        "project_id": "68pz8xfn",
        "dataset": "production",
        "label": "Gate Milano",
        "has_site_settings": True,
        "has_blog_posts": False,
    },
    "gate_sardinia": {
        "project_id": "1999xgdy",
        "dataset": "production",
        "label": "Gate Sardinia",
        "has_site_settings": False,
        "has_blog_posts": True,
    },
}

GROQ_EVENTS = """*[_type == "event" && date >= $today] | order(date asc) {
  _id,
  title,
  date,
  venue,
  artists,
  ticketUrl,
  isSoldOut,
  isSellingFast,
  genres,
  minAge,
  endDate,
  startTime,
  endTime,
  openingTime,
  closingTime,
  doorsTime,
  openingHours,
  hours
}"""

GROQ_SITE_SETTINGS = """*[_type == "siteSettings"][0] {
  venueName,
  description,
  tagline,
  address,
  email,
  bookingEmail,
  openingHours,
  instagram,
  googleMapsUrl
}"""

GROQ_BLOG_POSTS = """*[_type == "blogPost"] {
  _id,
  titleIt,
  bodyIt,
  titleEn,
  bodyEn
}"""


async def _sanity_get(project_id: str, dataset: str, query: str, params: dict = None,
                      token: str = "", perspective: str = "") -> dict:
    url = f"https://{project_id}.api.sanity.io/v{SANITY_API_VERSION}/data/query/{dataset}"
    req_params = {"query": query}
    if params:
        req_params.update(params)
    if perspective:
        req_params["perspective"] = perspective
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(url, params=req_params, headers=headers)
        r.raise_for_status()
        return r.json()


_XCEED_ID_RE = re.compile(r"xceed\.me/[^/]+/[^/]+/event/[^/]+/(\d+)")

def _extract_xceed_id(ticket_url: str) -> str:
    m = _XCEED_ID_RE.search(ticket_url or "")
    return m.group(1) if m else ""


async def _fetch_dice_description(ticket_url: str) -> str:
    """Extract event description from Dice.fm JSON-LD. Returns empty string on failure."""
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True, headers={"User-Agent": "Mozilla/5.0"}) as client:
            r = await client.get(ticket_url)
            if r.status_code != 200:
                return ""
            blocks = re.findall(r'<script type="application/ld\+json">(.*?)</script>', r.text, re.DOTALL)
            import json as _json
            for block in blocks:
                try:
                    data = _json.loads(block)
                    if data.get("@type") == "MusicEvent":
                        return (data.get("description") or "").strip()
                except Exception:
                    continue
    except Exception as e:
        logger.debug("Dice scrape failed for %s: %s", ticket_url, e)
    return ""


# --- TicketSMS (biglietteria Gate Sardinia) ---
# TicketSMS non ha un'API pubblica documentata, ma il backend che alimenta la SPA
# è raggiungibile in lettura senza auth: GET /api/v3/events/<codeUrl> restituisce
# descrizione + tipi biglietto con prezzi. codeUrl = slug nell'URL .../event/<slug>.
_TICKETSMS_API = "https://backend.ticketsms.it/api/v3/events"
_TICKETSMS_SLUG_RE = re.compile(r"ticketsms\.it/event/([^/?#]+)")


def _extract_ticketsms_slug(ticket_url: str) -> str:
    m = _TICKETSMS_SLUG_RE.search(ticket_url or "")
    return m.group(1) if m else ""


def _quill_to_text(raw: str) -> str:
    """Quill Delta JSON ({"ops":[{"insert":...}]}) → testo semplice. "" se non parsa."""
    if not raw:
        return ""
    try:
        ops = json.loads(raw).get("ops", [])
    except Exception:
        return raw if isinstance(raw, str) else ""
    return "".join(
        o.get("insert", "") for o in ops
        if isinstance(o, dict) and isinstance(o.get("insert"), str)
    ).strip()


def _parse_ticketsms_event(data: dict) -> dict:
    """Estrae {about, prices_str} dalla risposta v3 di TicketSMS. Non solleva.

    prices_str: la stringa 'a partire da €X' di TicketSMS + il prezzo minimo per
    settore (sempre un 'a partire da', quindi onesto anche se gli scaglioni cambiano).
    """
    result = {"about": "", "prices_str": ""}
    body = (data or {}).get("body") or []
    about = ""
    price_min_str = ""
    sector_min: dict[str, tuple[int, str]] = {}  # settore -> (centesimi, formatted)
    for comp in body:
        if not isinstance(comp, dict):
            continue
        if not price_min_str and comp.get("ticketsPriceMin"):
            price_min_str = str(comp["ticketsPriceMin"]).strip()
        for it in comp.get("list") or []:
            if not isinstance(it, dict):
                continue
            ct = it.get("componentType")
            if ct == "eventDetails" and not about:
                about = _quill_to_text(it.get("description") or "")
            elif ct == "ticket":
                price = it.get("price") or {}
                try:
                    cents = int(price.get("amount"))
                except (TypeError, ValueError):
                    continue
                fmt = price.get("formatted") or f"€{cents / 100:.2f}"
                sector = ((it.get("sector") or {}).get("name") or "Generale").strip() or "Generale"
                if sector not in sector_min or cents < sector_min[sector][0]:
                    sector_min[sector] = (cents, fmt)
    lines = []
    if price_min_str:
        lines.append(f"  {price_min_str}")
    for sector, (_cents, fmt) in sorted(sector_min.items(), key=lambda kv: kv[1][0]):
        lines.append(f"  - {sector}: a partire da {fmt}")
    result["about"] = about
    result["prices_str"] = "\n".join(lines)
    return result


async def _fetch_ticketsms_enrichment(ticket_url: str) -> dict:
    """Returns {about, prices_str} per un evento TicketSMS. Non solleva mai."""
    result = {"about": "", "prices_str": ""}
    slug = _extract_ticketsms_slug(ticket_url)
    if not slug:
        return result
    try:
        async with httpx.AsyncClient(
            timeout=10, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "it"}
        ) as client:
            r = await client.get(f"{_TICKETSMS_API}/{slug}")
            if r.status_code != 200:
                return result
            return _parse_ticketsms_event(r.json().get("data", {}))
    except Exception as e:
        logger.debug("TicketSMS enrichment failed for %s: %s", slug, e)
        return result


async def _fetch_xceed_enrichment(xceed_id: str, xceed_api_key: str) -> dict:
    """Returns {about, prices_str} for an Xceed event numeric ID. Never raises."""
    result = {"about": "", "prices_str": ""}
    if not xceed_id:
        return result
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"https://events.xceed.me/v1/events/{xceed_id}")
            if r.status_code != 200:
                return result
            data = r.json().get("data", {})
            result["about"] = (data.get("about") or "").strip()
            uuid = data.get("id", "")
            if not uuid or not xceed_api_key:
                return result
            r2 = await client.get(
                f"https://partner.xceed.me/v2/events/{uuid}/offers",
                headers={"X-API-Key": xceed_api_key},
            )
            if r2.status_code != 200:
                return result
            offers = r2.json().get("data", {})
            lines = []
            for cat in ("ticket", "guestlist"):
                for item in offers.get(cat, []):
                    if not isinstance(item, dict):
                        continue
                    name = item.get("name", "")
                    if isinstance(name, dict):
                        name = name.get("it") or name.get("en") or ""
                    price = item.get("priceAmount")
                    sold_out = item.get("isSoldOut", False)
                    hidden = item.get("isHidden", False)
                    if hidden or price is None:
                        continue
                    avail = " (ESAURITO)" if sold_out else ""
                    lines.append(f"  - {name}: €{price}{avail}")
            result["prices_str"] = "\n".join(lines)
    except Exception as e:
        logger.debug("Xceed enrichment failed for id=%s: %s", xceed_id, e)
    return result


async def _fetch_events(project_id: str, dataset: str) -> list[dict] | None:
    """Lista eventi futuri, o None su ERRORE di fetch. La distinzione []/None è
    fondamentale: [] = 'Sanity dice che non ci sono eventi' (ok svuotare lo store),
    None = 'non ho potuto chiedere a Sanity' (lo store esistente va PRESERVATO,
    altrimenti un errore di rete al sync delle 04:00 lascia il bot senza eventi
    fino al sync successivo e risponde 'non ho la programmazione')."""
    # Filtro sul GIORNO DI SERVIZIO (rollover 06:00), non sull'UTC: altrimenti un sync
    # tra le 00:00 UTC (02:00 Rome) e le 06:00 scarterebbe la serata di stanotte ancora
    # in corso (es. Perreo del sabato, aperto fino alle 03:00) trattandola come passata.
    from rag.date_utils import business_now
    today = business_now().strftime("%Y-%m-%d")
    from config import settings as _settings
    token = _settings.sanity_api_token
    try:
        # Con token leggiamo anche le BOZZE (previewDrafts): un evento creato in
        # Studio ma non ancora pubblicato esiste comunque per il bot. Senza token,
        # solo i pubblicati (l'API pubblica non restituisce i draft).
        data = await _sanity_get(
            project_id, dataset, GROQ_EVENTS, {"$today": f'"{today}"'},
            token=token, perspective="previewDrafts" if token else "",
        )
        return data.get("result", []) or []
    except Exception as e:
        logger.error("Sanity events fetch error (project=%s): %s", project_id, e)
        return None


async def _fetch_site_settings(project_id: str, dataset: str) -> dict:
    try:
        data = await _sanity_get(project_id, dataset, GROQ_SITE_SETTINGS)
        return data.get("result") or {}
    except Exception as e:
        logger.error("Sanity siteSettings fetch error (project=%s): %s", project_id, e)
        return {}


async def _fetch_blog_posts(project_id: str, dataset: str) -> list[dict]:
    try:
        data = await _sanity_get(project_id, dataset, GROQ_BLOG_POSTS)
        return data.get("result", []) or []
    except Exception as e:
        logger.error("Sanity blogPosts fetch error (project=%s): %s", project_id, e)
        return []


def _format_date(date_str: str) -> str:
    if not date_str:
        return "Data da definire"
    try:
        # Handle both "2026-05-02T21:00:00.000Z" and "2026-05-02"
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            dt_rome = dt.astimezone(_ROME)
            svc = _service_day(dt_rome)
            # Ora reale d'inizio; se è mezzanotte (data inserita senza orario) mostra
            # solo il giorno per non scrivere un fuorviante "ore 00:00".
            if dt_rome.hour == 0 and dt_rome.minute == 0:
                return svc.strftime("%-d %B %Y")
            return f"{svc.strftime('%-d %B %Y')}, ore {dt_rome.strftime('%H:%M')}"
        else:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.strftime("%-d %B %Y")
    except Exception:
        return date_str


_TIME_RE = re.compile(r"^\s*(\d{1,2})[:\.](\d{2})")


def _hhmm(value) -> str:
    """Normalizza un orario a 'HH:MM'. Accetta:
    - stringa 'HH:MM' / 'H.MM' / 'HH:MM:SS' → 'HH:MM'
    - datetime ISO ('...T18:30:00Z' / con offset) → l'ora in fuso Rome
    Ritorna '' se non riconosciuto o se è mezzanotte 'vuota' (data senza orario)."""
    if value in (None, "", 0):
        return ""
    s = str(value).strip()
    # ISO datetime (contiene 'T' e una data): prendi l'ora in fuso Rome.
    if "T" in s and "-" in s[:8]:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            dt_rome = dt.astimezone(_ROME)
            if dt_rome.hour == 0 and dt_rome.minute == 0:
                return ""  # mezzanotte = data inserita senza orario reale
            return dt_rome.strftime("%H:%M")
        except Exception:
            return ""
    m = _TIME_RE.match(s)
    if m:
        h, mm = int(m.group(1)), m.group(2)
        if 0 <= h <= 23:
            return f"{h:02d}:{mm}"
    return ""


def _extract_hours(event: dict) -> str:
    """Orario della SINGOLA serata letto da Sanity (fonte di verità, editabile in CMS
    senza deploy). Serve per eventi con orari NON standard, es. un opening party
    18:30–20:30 diverso dalle serate club 22:00–04:00. Ritorna la riga 'Orari: ...'
    (senza newline) o '' se Sanity non fornisce orari per l'evento.

    Campi Sanity supportati (tutti opzionali):
    - openingHours / hours: stringa libera → usata verbatim (es. '18:30 - 20:30').
    - startTime/openingTime + endTime/closingTime: orari 'HH:MM'.
    - date (con orario) → inizio; endDate (con orario) → fine.
    Priorità: stringa libera > campi start/end espliciti > orari dentro date/endDate."""
    free = (event.get("openingHours") or event.get("hours") or "")
    if isinstance(free, str) and free.strip():
        return f"Orari: {free.strip()}"

    # `date` come inizio vale SOLO in coppia con una fine esplicita: da solo ogni evento
    # ne ha uno (Milano ha sempre l'orario nel `date`) e produrrebbe una falsa riga
    # "apertura HH:MM" a un solo estremo che sovrascrive il default della venue.
    start = _hhmm(event.get("startTime") or event.get("openingTime") or event.get("doorsTime")) \
        or _hhmm(event.get("date"))
    end = _hhmm(event.get("endTime") or event.get("closingTime")) or _hhmm(event.get("endDate"))

    # Solo con ENTRAMBI gli estremi: una riga a un estremo solo cancellerebbe l'altro
    # (il prompt dice di rispondere con gli estremi della riga "Orari:"). Se manca un
    # estremo, niente riga → vale il default 22:00–04:00. Per orari particolari a un
    # solo estremo o non standard, usare il campo libero openingHours (es. "fino alle 02:00").
    if start and end:
        return f"Orari: {start} - {end}"
    return ""


# Orario di apertura standard di Gate Sardinia: apertura 22:00, chiusura 03:00, TUTTE
# le sere (fisso). Calcolato/inserito in codice e messo nel documento come riga "Orari:"
# così il bot legge l'orario già pronto e non lo deduce.
_SARDINIA_HOURS = "22:00 - 03:00"


def _sardinia_default_hours(date_str: str = "") -> str:
    """Finestra oraria di apertura standard di Gate Sardinia (22:00–03:00, tutte le sere).
    date_str è tenuto per compatibilità (in passato l'orario variava per giorno)."""
    return _SARDINIA_HOURS


def _build_document(event: dict, venue_label: str, xceed: dict = None) -> tuple[str, dict]:
    # Titoli placeholder ("?????", vuoti): l'evento ESISTE in cartellone e va comunque
    # indicizzato — data, sala, prezzi e link biglietti sono informazioni vere. Prima
    # questi eventi venivano filtrati via nella GROQ e il bot rispondeva "non ho la
    # programmazione" pur avendo l'evento su Sanity (caso reale sabato 4 luglio).
    raw_title = (event.get("title") or "").strip()
    is_tba = not raw_title or not raw_title.strip("?. …")
    title = "Serata in programma (line-up da annunciare)" if is_tba else raw_title
    # Bozza Sanity (letta via previewDrafts): l'id è "drafts.<id>". La normalizziamo
    # all'id pubblicato (per store, dedup e link /tavoli?event=<id>) e segnaliamo nel
    # documento che i dettagli sono in conferma, così il bot non li vende per definitivi.
    is_draft = (event.get("_id") or "").startswith("drafts.")
    date_str = event.get("date", "")
    room = event.get("venue") or ""
    ticket_url = event.get("ticketUrl") or ""
    is_sold_out = event.get("isSoldOut") or False
    is_selling_fast = event.get("isSellingFast") or False
    # Le liste da Sanity possono contenere null o riferimenti non risolti: teniamo
    # solo le stringhe, un valore sporco non deve far saltare l'indicizzazione.
    genres = [g for g in (event.get("genres") or []) if isinstance(g, str) and g.strip()]
    min_age = event.get("minAge")
    # Lineup completa (Sanity `artists`): può contenere artisti NON presenti nel
    # titolo. Va nel documento (così il bot sa dire chi suona) e nei metadata (così
    # find_event_dates_by_name risolve la data anche dal nome di un artista in lineup).
    artists = [a.strip() for a in (event.get("artists") or [])
               if isinstance(a, str) and a.strip()]

    date_fmt = _format_date(date_str)
    # Orari della serata: prima l'override esplicito da Sanity (editabile in CMS, es. un
    # opening party 18:30–20:30); in assenza, per Gate Sardinia usiamo la finestra
    # standard fissa (22:00–03:00, tutte le sere), messa già pronta nel documento così
    # il bot la legge invece di dedurre orari.
    hours_line = _extract_hours(event)
    if not hours_line and venue_label == "Gate Sardinia":
        win = _sardinia_default_hours(date_str)
        if win:
            hours_line = f"Orari: {win}"
    hours_str = f"\n{hours_line}" if hours_line else ""
    room_str = f"\nSala: {room}" if room else ""
    genres_str = f"\nGeneri: {', '.join(genres)}" if genres else ""
    lineup_str = f"\nLineup: {', '.join(artists)}" if artists else ""
    # Età minima per-evento da Sanity. Accetta numero (16/18) o stringa ("16+", "18+").
    # Se valorizzata è ESPLICITA e prioritaria per il bot (vedi regola ETÀ nel system prompt).
    age_str = ""
    if min_age not in (None, "", 0):
        age_label = f"{min_age}+" if isinstance(min_age, (int, float)) else str(min_age).strip()
        age_str = f"\nEtà minima: {age_label} (documento obbligatorio)"

    ticket_str = ""
    if ticket_url:
        if is_sold_out:
            ticket_str = f"\nBiglietti: ESAURITI — {ticket_url}"
        elif is_selling_fast:
            ticket_str = f"\nBiglietti: 🔥 Sold out velocemente — Acquista: {ticket_url}"
        else:
            ticket_str = f"\nAcquista biglietti: {ticket_url}"

    xceed = xceed or {}
    prices_str = f"\nPrezzi:\n{xceed['prices_str']}" if xceed.get("prices_str") else ""
    about = xceed.get("about", "")
    about_str = f"\nDescrizione: {about[:600]}" if about else ""

    draft_str = "\nNB: dettagli in via di conferma (evento non ancora pubblicato sul sito)" if is_draft else ""
    # Le serate col titolo "?????" sono TOP SECRET di proposito (headliner a
    # sorpresa): il bot deve confermare la data e creare attesa per l'annuncio,
    # NON dire "non ho informazioni".
    tba_str = (
        "\nNB: data CONFERMATA, line-up top secret — l'annuncio ufficiale arriverà "
        "a breve sui canali della venue (Instagram): si preannuncia una serata "
        "da non perdere, tieni d'occhio il profilo."
    ) if is_tba else ""

    document = (
        f"EVENTO: {title}\n"
        f"Venue: {venue_label}"
        f"{room_str}\n"
        f"Data: {date_fmt}"
        f"{hours_str}"
        f"{lineup_str}"
        f"{genres_str}"
        f"{age_str}"
        f"{about_str}"
        f"{prices_str}"
        f"{ticket_str}"
        f"{tba_str}"
        f"{draft_str}"
    ).strip()

    # date_ts: midnight UTC del GIORNO DI SERVIZIO (rollover −6h, vedi _service_day).
    # Es: "2026-07-04T22:00Z" = "2026-07-05 00:00 Rome" → serata del 4 → date_ts = 4 luglio.
    # Coerente con event_store._today_start_utc / date_utils.business_now, così una serata
    # a cavallo di mezzanotte è indicizzata sul giorno che l'utente intende ("questo sabato").
    date_ts = 0
    try:
        from datetime import datetime, timezone as tz
        if "T" in date_str:
            dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            dt_rome = dt.astimezone(_ROME)
            svc = _service_day(dt_rome)
            date_ts = int(datetime(svc.year, svc.month, svc.day, tzinfo=tz.utc).timestamp())
        else:
            date_ts = int(datetime.strptime(date_str[:10], "%Y-%m-%d").replace(tzinfo=tz.utc).timestamp())
    except Exception:
        pass

    metadata = {
        "type": "event",
        "source": "sanity",
        "event_name": title,
        "artists": artists,
        "date": date_str,
        "date_ts": date_ts,
        "venue": venue_label,
        # id pubblicato anche per le bozze ("drafts.<id>" → "<id>"): usato per i
        # link /tavoli?event=<id> e per il dedup col documento post-publish.
        "sanity_id": (event.get("_id") or "").removeprefix("drafts."),
        "ticket_url": ticket_url,
    }
    return document, metadata


def _portable_text_to_str(blocks: list) -> str:
    """Extract plain text from Sanity Portable Text block array."""
    if not blocks:
        return ""
    lines = []
    for block in blocks:
        if not isinstance(block, dict) or block.get("_type") != "block":
            continue
        text = "".join(
            span.get("text", "") for span in block.get("children", [])
            if isinstance(span, dict) and span.get("_type") == "span"
        )
        if text.strip():
            lines.append(text.strip())
    return "\n\n".join(lines)


def _build_site_settings_document(settings: dict, venue_label: str) -> tuple[str, dict]:
    name = settings.get("venueName") or venue_label
    desc = settings.get("description") or ""
    tagline = settings.get("tagline") or ""
    addr = settings.get("address") or {}
    street = addr.get("street", "")
    city = addr.get("city", "")
    postal = addr.get("postalCode", "")
    email = settings.get("email") or ""
    booking_email = settings.get("bookingEmail") or ""
    hours = settings.get("openingHours") or ""
    ig = settings.get("instagram") or ""
    maps = settings.get("googleMapsUrl") or ""

    parts = [f"VENUE: {name}"]
    if tagline:
        parts.append(tagline)
    if desc:
        parts.append(desc)
    if street:
        parts.append(f"Indirizzo: {street}, {postal} {city}".strip(", "))
    if hours:
        parts.append(f"Orari: {hours}")
    if email:
        parts.append(f"Email: {email}")
    if booking_email and booking_email != email:
        parts.append(f"Booking: {booking_email}")
    if ig:
        parts.append(f"Instagram: @{ig}")
    if maps:
        parts.append(f"Google Maps: {maps}")

    document = "\n".join(parts)
    metadata = {
        "type": "site_settings",
        "source": "sanity",
        "venue": venue_label,
    }
    return document, metadata


def _build_blog_document(post: dict, venue_label: str) -> tuple[str, dict]:
    # I post sono bilingui (it/en). I clienti scrivono soprattutto in italiano, ma
    # qualcuno scrive in inglese: indicizziamo ENTRAMBE le lingue così il RAG trova
    # il contenuto a prescindere dalla lingua della domanda. Titolo: italiano primario.
    title_it = post.get("titleIt")
    title_en = post.get("titleEn")
    titles = " / ".join(dict.fromkeys(t for t in (title_it, title_en) if t))
    titles = titles or post.get("title") or "Info"
    body_it = _portable_text_to_str(post.get("bodyIt") or [])
    body_en = _portable_text_to_str(post.get("bodyEn") or post.get("body") or [])
    body = "\n\n".join(p for p in (body_it, body_en) if p)
    document = f"{titles}\n\n{body}".strip()
    metadata = {
        "type": "blog_post",
        "source": "sanity",
        "venue": venue_label,
        "sanity_id": post.get("_id", ""),
    }
    return document, metadata


async def sync_all_venues():
    logger.info("Avvio sync Sanity...")

    for venue_key, cfg in SANITY_PROJECTS.items():
        label = cfg["label"]
        project_id = cfg["project_id"]
        dataset = cfg["dataset"]
        status = {"ok": False, "fetched": 0, "indexed": 0, "skipped_bad": 0, "error": "",
                  "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        _last_sync[venue_key] = status

        # Events. Se il fetch FALLISCE (None) non tocchiamo lo store: meglio dati
        # di qualche ora fa che nessun dato — senza questa guardia un errore di rete
        # cancellava TUTTI gli eventi della venue (delete_stale con lista vuota) e il
        # bot rispondeva "non ho la programmazione" fino al sync successivo.
        events = await _fetch_events(project_id, dataset)
        if events is None:
            from rag.event_store import count as _count
            status["error"] = "fetch fallito — store preservato"
            logger.error(
                "Sanity: fetch eventi FALLITO per %s — mantengo i %d eventi già in memoria",
                label, _count(venue_key),
            )
        from config import settings as _settings
        current_ids = []
        if events is not None:
            status["fetched"] = len(events)
            for event in events:
                # Ogni evento è isolato: una SINGOLA scheda malformata (es. ticketUrl
                # null — bug reale che azzerava Gate Sardinia: "dice.fm" in None →
                # TypeError al primo evento TBA) non deve mai più far saltare
                # l'intera venue. La scheda cattiva si salta e si logga.
                try:
                    # id pubblicato anche per le bozze: così quando l'evento viene
                    # pubblicato sostituisce la sua bozza nello store invece di duplicarla.
                    sanity_id = (event.get("_id") or "").removeprefix("drafts.")
                    if not sanity_id:
                        continue
                    # NB: .get("ticketUrl", "") NON basta — Sanity ritorna la chiave
                    # con valore null e .get restituirebbe None.
                    ticket_url = event.get("ticketUrl") or ""
                    xceed_id = _extract_xceed_id(ticket_url)
                    if xceed_id:
                        xceed_data = await _fetch_xceed_enrichment(xceed_id, _settings.xceed_api_key)
                    elif "dice.fm" in ticket_url:
                        desc = await _fetch_dice_description(ticket_url)
                        xceed_data = {"about": desc, "prices_str": ""}
                    elif "ticketsms" in ticket_url:
                        xceed_data = await _fetch_ticketsms_enrichment(ticket_url)
                    else:
                        xceed_data = {"about": "", "prices_str": ""}
                    doc, meta = _build_document(event, label, xceed_data)
                    upsert_event(venue_key, sanity_id, doc, meta)
                    current_ids.append(sanity_id)
                except Exception:
                    status["skipped_bad"] += 1
                    logger.exception("Sanity: evento malformato saltato (%s, id=%s)",
                                     label, event.get("_id"))
            status["indexed"] = len(current_ids)
            status["ok"] = True
            logger.info("Sanity: %d eventi futuri per %s (%d indicizzati, %d saltati)",
                        len(events), label, len(current_ids), status["skipped_bad"])
            delete_stale_events(venue_key, current_ids, source="sanity")

        # Site settings (Milano only) — isolato: un errore qui non deve bloccare
        # il sync della venue successiva.
        try:
            if cfg.get("has_site_settings"):
                settings = await _fetch_site_settings(project_id, dataset)
                if settings:
                    doc, meta = _build_site_settings_document(settings, label)
                    upsert_event(venue_key, f"site_settings_{venue_key}", doc, meta)
                    logger.info("Sync siteSettings per %s", label)
        except Exception:
            logger.exception("Sanity: siteSettings falliti per %s — continuo", label)

        # Blog posts (Sardinia only) — isolato come sopra.
        try:
            if cfg.get("has_blog_posts"):
                posts = await _fetch_blog_posts(project_id, dataset)
                logger.info("Sanity: %d blog posts per %s", len(posts), label)
                for post in posts:
                    post_id = post.get("_id", "")
                    if not post_id:
                        continue
                    body_text = _portable_text_to_str(post.get("bodyEn") or post.get("body") or [])
                    if not body_text:
                        continue
                    doc, meta = _build_blog_document(post, label)
                    upsert_event(venue_key, post_id, doc, meta)
        except Exception:
            logger.exception("Sanity: blog posts falliti per %s — continuo", label)

        logger.info("Sync Sanity completato per %s: %d eventi", label, len(current_ids))

    from rag.knowledge_cache import invalidate
    invalidate()
    logger.info("Sync Sanity completato — knowledge cache invalidata.")
