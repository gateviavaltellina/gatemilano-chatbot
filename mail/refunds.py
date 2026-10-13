"""Rimborsi: legge rimborsi@, verifica la richiesta e la porta allo staff già istruita.

Policy (staff 13/10):
- si rimborsa SOLO chi, con un biglietto valido, è stato respinto all'ingresso;
- servono prova d'acquisto, biglietti e la motivazione (cosa è successo alla porta):
  chi semplicemente non è venuto non si rimborsa;
- entro il lunedì successivo all'evento; importo pieno, commissioni incluse.

Per ogni cliente (si ragiona per mittente: spesso manda foto e testo in mail separate):
1. il modello legge testo e allegati (screenshot del biglietto e del pagamento) e dice
   cosa c'è e cosa manca;
2. si cercano i biglietti su Xceed: evento, se sono stati usati, se sono ancora validi.
   La selezione avviene prima della scansione: un biglietto usato vuol dire che il
   cliente è entrato;
3. esito:
   - manca qualcosa dal cliente → gli si scrive cosa manca (testo fisso);
   - completa → conferma di ricezione (testo fisso) e scheda su Discord: lo staff
     rimborsa con `!rimborsa` (o da Xceed e poi `!fatto`), oppure `!rifiuta`.
Il bot non promette mai un esito: decide lo staff, finché REFUND_AUTO_EXECUTE è spento.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ai.claude_client import _client, compat_kwargs
from config import settings
from mail import gmail_client as gm
from mail import templates, triage
from mail import xceed_tickets as xt

logger = logging.getLogger(__name__)

LABEL_SEEN = "Bot/Rimborso"            # ogni messaggio a rimborsi@ già letto dal bot
LABEL_BOT_SENT = "Bot/Inviato"         # risposte inviate dal bot (≠ risposte dello staff)
LABEL_ACK = "Bot/Rimborso ricevuto"    # conferma di ricezione già mandata
LABEL_DONE = "Bot/Rimborso fatto"
LABEL_REFUSED = "Bot/Rimborso rifiutato"

_ROME = ZoneInfo("Europe/Rome")
_IMAGE_MIMES = {"image/jpeg", "image/png", "image/gif", "image/webp"}
_MAX_BYTES = 4_500_000
_MAX_FILES = 8

_SYSTEM = """Analizzi le richieste di rimborso arrivate a un locale notturno (Gate Milano).
Leggi TUTTI i messaggi e gli allegati (screenshot di biglietti, conferme Xceed, ricevute
di pagamento) e rispondi SOLO chiamando lo strumento.

Policy: si rimborsa solo chi aveva un biglietto valido ed è stato RESPINTO ALL'INGRESSO
dalla sicurezza. Chi non è venuto per motivi propri (lavoro, malattia, ritardo,
imprevisti, ha cambiato idea) NON rientra.

- respinto_alla_porta: true solo se il cliente dice di essere stato respinto / non fatto
  entrare / selezionato all'ingresso.
- motivazione: con parole tue, cosa racconta il cliente di quello che è successo alla
  porta. Vuoto se non lo spiega (es. scrive solo "rimborso" o manda solo foto).
- non_venuto: true se dice di non essere venuto per motivi propri.
- prova_acquisto: true se c'è una conferma d'ordine, ricevuta o screenshot del pagamento.
- biglietti: true se c'è il biglietto (PDF, QR code, conferma Xceed con i biglietti).
- codici: numeri di prenotazione/biglietto/ordine e codici QR LEGGIBILI negli allegati o
  nel testo. Non inventarne.
- nomi: nomi e cognomi degli intestatari visibili.
- email_acquisto: email usate per l'acquisto, se visibili.
- data_evento: YYYY-MM-DD della serata se deducibile, altrimenti vuoto.
- lingua: "it" se scrive in italiano, altrimenti "en"."""

_TOOL = {
    "name": "analizza_rimborso",
    "description": "Registra cosa contiene la richiesta di rimborso.",
    "input_schema": {
        "type": "object",
        "properties": {
            "respinto_alla_porta": {"type": "boolean"},
            "motivazione": {"type": "string"},
            "non_venuto": {"type": "boolean"},
            "prova_acquisto": {"type": "boolean"},
            "biglietti": {"type": "boolean"},
            "codici": {"type": "array", "items": {"type": "string"}},
            "nomi": {"type": "array", "items": {"type": "string"}},
            "email_acquisto": {"type": "array", "items": {"type": "string"}},
            "evento": {"type": "string"},
            "data_evento": {"type": "string"},
            "importo": {"type": "string"},
            "lingua": {"type": "string"},
        },
        "required": ["respinto_alla_porta", "motivazione", "non_venuto", "prova_acquisto",
                     "biglietti", "codici", "nomi", "lingua"],
    },
}


# --- logica pura (testabile) ---------------------------------------------------

def refund_deadline(event_day: str) -> datetime:
    """Fine del lunedì successivo alla serata (ora di Milano)."""
    d = datetime.strptime(event_day, "%Y-%m-%d").replace(tzinfo=_ROME)
    days = (7 - d.weekday()) % 7 or 7          # lunedì DOPO la serata
    return (d + timedelta(days=days)).replace(hour=23, minute=59, second=59)


def missing_from_customer(a: dict) -> list[str]:
    missing = []
    if not a.get("prova_acquisto"):
        missing.append("prova_acquisto")
    if not a.get("biglietti"):
        missing.append("biglietti")
    if not (a.get("respinto_alla_porta") and (a.get("motivazione") or "").strip()):
        missing.append("motivazione")
    return missing


def assess(a: dict, found: dict, first_request: datetime) -> dict:
    """Esito della verifica: stato, cosa manca al cliente, controlli lato Xceed."""
    if a.get("non_venuto") and not a.get("respinto_alla_porta"):
        return {"status": "non_rimborsabile", "missing": [], "checks": [
            ("❌", "dice di non essere venuto per motivi propri: non rientra nella policy")]}
    missing = missing_from_customer(a)
    if missing:
        return {"status": "incompleta", "missing": missing, "checks": []}

    checks: list[tuple[str, str]] = [("✅", f"motivazione: {a.get('motivazione','').strip()[:300]}"),
                                     ("✅", "prova d'acquisto e biglietti allegati")]
    if found.get("error"):
        checks.append(("⚠️", f"verifica Xceed non riuscita: {found['error']}"))
    elif not found.get("tickets"):
        checks.append(("❌", "biglietti NON trovati su Xceed (email, codici e nomi non corrispondono)"))
    else:
        s = xt.summarize(found["tickets"])
        ev = found["event"]
        day = xt.event_day(ev)
        how = {"codice": "per codice", "email": "per email", "nome": "solo per NOME (verifica)"}[found["match"]]
        checks.append(("✅" if found["match"] != "nome" else "⚠️",
                       f"trovati {s['count']} biglietti {how}: {ev.get('name')} del {day}"
                       + (f" · {s['offer']} {s['price']}" if s["offer"] else "")))
        checks.append(("❌", f"{s['used']} biglietti risultano USATI all'ingresso: il cliente è entrato")
                      if s["used"] else ("✅", "biglietti non usati all'ingresso"))
        if s["inactive"]:
            checks.append(("❌", f"{s['inactive']} biglietti non più validi (già rimborsati o annullati?)"))
        deadline = refund_deadline(day)
        checks.append(("✅", f"richiesta nei tempi (scadenza {deadline:%d/%m})")
                      if first_request <= deadline else
                      ("❌", f"richiesta FUORI TEMPO: scadenza {deadline:%d/%m}, arrivata il {first_request:%d/%m}"))
    status = "ok" if all(c[0] == "✅" for c in checks) else "da_verificare"
    return {"status": status, "missing": [], "checks": checks}


# --- Gmail e modello ---------------------------------------------------------------

def _query_new() -> str:
    return (f"deliveredto:{settings.refund_inbox} newer_than:{settings.refund_window_days}d "
            f'-label:"{LABEL_SEEN}" -in:sent')


def _query_sender(sender: str, label: str = "") -> str:
    q = f"deliveredto:{settings.refund_inbox} from:{sender} newer_than:{settings.refund_window_days + 7}d -in:sent"
    return q + (f' label:"{label}"' if label else "")


async def _has_label(sender: str, label: str) -> bool:
    return bool(await gm.list_unprocessed(_query_sender(sender, label), 1))


async def _content_blocks(msgs: list[dict]) -> list[dict]:
    lines = []
    blocks: list[dict] = []
    for m in sorted(msgs, key=lambda m: m.get("date_ms", 0)):
        when = datetime.fromtimestamp(m.get("date_ms", 0) / 1000, _ROME).strftime("%d/%m %H:%M")
        names = ", ".join(a["filename"] for a in m.get("attachments", [])) or "nessuno"
        lines.append(f"--- Messaggio del {when}\nOggetto: {m.get('subject','')}\n"
                     f"{(m.get('body') or '').strip()[:3000]}\nAllegati: {names}")
        for att in m.get("attachments", []):
            if len(blocks) >= _MAX_FILES or att.get("size", 0) > _MAX_BYTES:
                continue
            mime = att.get("mime", "").lower()
            if mime not in _IMAGE_MIMES and mime != "application/pdf":
                continue
            try:
                data = base64.b64encode(await gm.get_attachment(m["id"], att["attachment_id"])).decode()
            except Exception as e:
                logger.warning("Allegato %s non scaricato: %s", att.get("filename"), e)
                continue
            kind = "document" if mime == "application/pdf" else "image"
            blocks.append({"type": kind, "source": {"type": "base64", "media_type": mime, "data": data}})
    return [{"type": "text", "text": "\n\n".join(lines)}, *blocks]


async def analyze(msgs: list[dict]) -> dict:
    model = settings.model
    try:
        resp = await _client.messages.create(
            model=model, max_tokens=800, system=_SYSTEM, tools=[_TOOL],
            tool_choice={"type": "tool", "name": "analizza_rimborso"},
            messages=[{"role": "user", "content": await _content_blocks(msgs)}],
            **compat_kwargs(model, 0),
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                return dict(block.input or {})
    except Exception as e:
        logger.error("Analisi richiesta di rimborso fallita: %s", e)
    return {}


# --- flusso ------------------------------------------------------------------------

async def _reply(msg: dict, text: str) -> None:
    # Reply-To rimborsi@: la risposta parte da george@, ma se il cliente manda il
    # documento mancante deve tornare qui, dove il bot lo vede.
    await gm.send_reply(msg, text, label=LABEL_BOT_SENT, reply_to=settings.refund_inbox)


async def _label_all(msgs: list[dict], label: str) -> None:
    for m in msgs:
        try:
            await gm.add_label(m["id"], label)
        except Exception:
            logger.debug("Etichetta %s non applicata", label, exc_info=True)


async def _notify(sender: str, msgs: list[dict], a: dict, verdict: dict, found: dict,
                  note: str = "") -> None:
    try:
        from notifications.discord import notify_refund
        await notify_refund(sender, msgs, a, verdict, found, note=note)
    except Exception:
        logger.debug("Notifica Discord rimborso non riuscita", exc_info=True)


async def _auto_refund(latest: dict, msgs: list[dict], found: dict, lang: str) -> bool:
    """Rimborso senza intervento umano (REFUND_AUTO_EXECUTE). False se non possibile."""
    from mail import xceed_mcp
    booking_ids = xt.summarize(found.get("tickets", [])).get("booking_ids", [])
    if not (xceed_mcp.configured() and settings.xceed_mcp_refund_tool and booking_ids):
        return False
    try:
        for bid in booking_ids:
            await xceed_mcp.refund_booking(bid, lang)
    except Exception as e:
        logger.error("Rimborso automatico fallito: %s", e)
        return False
    await _reply(latest, templates.refund_done(lang, (found.get("event") or {}).get("name", "")))
    await _label_all(msgs, LABEL_DONE)
    return True


async def process_sender(sender: str, new_msgs: list[dict]) -> str:
    """Valuta TUTTE le mail recenti del mittente a rimborsi@ e agisce. Ritorna l'esito."""
    ids = await gm.list_unprocessed(_query_sender(sender), 20)
    msgs = [await gm.get_message(i) for i in ids] or new_msgs
    msgs.sort(key=lambda m: m.get("date_ms", 0))
    latest = msgs[-1]

    # Già chiusa (rimborsata o rifiutata) o seguita a mano dallo staff: il bot non
    # risponde più, segnala solo il nuovo messaggio.
    for label, what in ((LABEL_DONE, "già rimborsata"), (LABEL_REFUSED, "già rifiutata")):
        if await _has_label(sender, label):
            await _label_all(new_msgs, LABEL_SEEN)
            await _notify(sender, new_msgs, {}, {"status": "nota", "missing": [], "checks": []}, {},
                          note=f"Nuovo messaggio su una richiesta {what}.")
            return f"nota ({what})"
    for tid in {m["thread_id"] for m in msgs}:
        if await gm.thread_staff_replied(tid, LABEL_BOT_SENT):
            await _label_all(new_msgs, LABEL_SEEN)
            await _notify(sender, new_msgs, {}, {"status": "nota", "missing": [], "checks": []}, {},
                          note="Lo staff ha già risposto a mano: il bot non interviene.")
            return "nota (staff)"

    a = await analyze(msgs)
    if not a:
        await _label_all(new_msgs, LABEL_SEEN)
        await _notify(sender, msgs, {}, {"status": "errore", "missing": [], "checks": [
            ("⚠️", "analisi automatica non riuscita: guardala a mano")]}, {})
        return "errore-analisi"

    found: dict = {}
    if not missing_from_customer(a):
        found = await xt.find_request_tickets(
            {sender, *a.get("email_acquisto", [])}, set(a.get("codici", [])),
            set(a.get("nomi", [])), a.get("data_evento", ""))
    first = datetime.fromtimestamp(msgs[0].get("date_ms", 0) / 1000, _ROME)
    verdict = assess(a, found, first)
    lang = a.get("lingua", "it")

    if (verdict["status"] == "ok" and settings.refund_auto_execute
            and await _auto_refund(latest, msgs, found, lang)):
        await _label_all(new_msgs, LABEL_SEEN)
        await _notify(sender, msgs, a, verdict, found, note="Rimborsato in automatico.")
        return "rimborsato"
    if verdict["status"] == "incompleta":
        await _reply(latest, templates.refund_missing(lang, verdict["missing"]))
    elif verdict["status"] in ("ok", "da_verificare") and not await _has_label(sender, LABEL_ACK):
        await _reply(latest, templates.refund_ack(lang))
        await _label_all(msgs, LABEL_ACK)
    await _label_all(new_msgs, LABEL_SEEN)
    await _notify(sender, msgs, a, verdict, found)
    return verdict["status"]


async def run_once() -> dict:
    if not settings.refunds_enabled:
        return {"skipped": "disattivato"}
    if not gm.configured():
        return {"skipped": "credenziali mancanti"}
    try:
        ids = await gm.list_unprocessed(_query_new(), 30)
    except Exception as e:
        logger.error("Lettura rimborsi@ fallita: %s", e)
        return {"error": str(e)[:200]}

    by_sender: dict[str, list[dict]] = {}
    for mid in ids:
        try:
            m = await gm.get_message(mid)
        except Exception as e:
            logger.error("Lettura email %s fallita: %s", mid, e)
            continue
        if triage.hard_skip(m, box=settings.refund_inbox):
            await gm.add_label(mid, LABEL_SEEN)
            continue
        by_sender.setdefault(m["from_email"], []).append(m)

    esiti: dict[str, int] = {}
    for sender, msgs in by_sender.items():
        try:
            esito = await process_sender(sender, msgs)
        except Exception as e:
            logger.exception("Rimborso di %s non gestito: %s", sender, e)
            esito = "errore"
        key = esito.split(" (")[0]
        esiti[key] = esiti.get(key, 0) + 1
    if by_sender:
        logger.info("Rimborsi: %d mittenti — %s", len(by_sender), esiti)
    return {"mittenti": len(by_sender), "esiti": esiti}
