"""Risponditore email: legge info@, risponde alle richieste semplici, abbozza il resto.

Riusa lo STESSO cervello delle chat — `build_rag_context` + `generate_response` — così
quello che il bot sa su WhatsApp e Instagram lo sa anche via email: calendario eventi,
tavoli con disponibilità e link reali, policy età, orari. Nessuna knowledge base
parallela da tenere allineata.

Comportamento (deciso con lo staff il 7/10):
- categorie fattuali e verificabili → risposta inviata da sola;
- tutto il resto, e ogni dubbio → bozza in Gmail + avviso su Discord.

Tre interruttori, dal più grosso al più fine:
1. `mail_autoresponder_enabled`: se False il job non parte proprio.
2. `mail_draft_only`: prepara sempre bozze, non invia mai. È il default.
3. `mail_max_per_run`: tetto di messaggi per giro.
"""
from __future__ import annotations

import logging

from config import settings
from mail import gmail_client as gm
from mail import triage

logger = logging.getLogger(__name__)

# Etichette applicate ai messaggi già visti: sono anche la memoria anti-doppione,
# perché sopravvivono al riavvio del container (lo store in RAM no).
LABEL_DONE = "Bot/Risposto"
LABEL_REVIEW = "Bot/Da rivedere"
LABEL_SKIPPED = "Bot/Ignorato"



def _query() -> str:
    """Solo posta arrivata a info@ negli ultimi giorni e non ancora toccata dal bot.

    info@ è una casella Aruba inoltrata su una Gmail PERSONALE: senza il filtro
    `deliveredto:` il bot leggerebbe e risponderebbe a tutta la posta dell'account.
    """
    # Niente "is:unread": basta aprire la mail dal telefono, o che un'altra
    # automazione la segni letta (caso reale 14/10, proposta Michael Marchi), e il bot
    # la saltava. Contro i doppioni bastano le etichette Bot/…; contro le risposte già
    # date a mano c'è il controllo sul thread.
    return (
        f"deliveredto:{settings.mail_from_address} in:inbox "
        f"newer_than:{settings.mail_max_age_days}d "
        f'-label:"{LABEL_DONE}" -label:"{LABEL_REVIEW}" -label:"{LABEL_SKIPPED}"'
    )

_FOOTER_AUTO = (
    "\n\n—\n"
    "Risposta inviata automaticamente dall'assistente di Gate Milano. "
    "Se ti serve altro rispondi pure a questa email: ti legge una persona."
)
_FOOTER_AUTO_EN = (
    "\n\n—\n"
    "This reply was sent automatically by Gate Milano's assistant. "
    "If you need anything else, just reply to this email: a person will read it."
)


def _email_text(msg: dict) -> str:
    return f"{msg.get('subject','')}\n{msg.get('body','')}"


def _footer(msg: dict) -> str:
    """Nota di risposta automatica nella lingua del cliente (italiano o inglese)."""
    from ai.language import detect_language
    lang = detect_language(_email_text(msg))
    return _FOOTER_AUTO if lang in (None, "italiano") else _FOOTER_AUTO_EN


def _venue_for(msg: dict) -> str:
    """A quale sede si riferisce la mail. In dubbio Milano: è la casella info@ di Milano."""
    text = f"{msg.get('subject','')} {msg.get('body','')}".lower()
    if "sardinia" in text or "sardegna" in text or "budoni" in text:
        return "gate_sardinia"
    return "gate_milano"


async def _compose(msg: dict, verdict: dict, prior: str = "") -> str:
    """Testo della risposta, generato col contesto reale di eventi e tavoli."""
    from ai.claude_client import generate_response
    from rag.context_builder import build_rag_context

    venue = _venue_for(msg)
    # La domanda ricostruita dal classificatore guida il contesto; il corpo originale
    # va comunque al modello, così non si perde nulla di quello che ha scritto.
    query = verdict.get("domanda") or msg.get("subject", "")
    rag_context, _dates = await build_rag_context(venue, f"{query}\n{msg.get('body','')}")
    previous = (
        "QUESTA È LA RISPOSTA DEL CLIENTE A UNA NOSTRA MAIL. La nostra mail precedente era:\n"
        f"---\n{prior[:2000]}\n---\nRispondi tenendo conto di quello che gli abbiamo già "
        "scritto: non ripeterlo, rispondi a quello che chiede adesso.\n\n"
    ) if prior else ""
    user_message = (
        previous +
        f"Email ricevuta su {settings.mail_from_address}.\n"
        f"Oggetto: {msg.get('subject','')}\n\n"
        f"{msg.get('body','')}\n\n"
        "Scrivi la RISPOSTA EMAIL: saluto iniziale e risposta puntuale a ogni domanda "
        "posta. Niente oggetto, niente formula di chiusura (\"Un saluto\", \"Cordiali "
        "saluti\"...) e niente firma: la firma dello staff, che apre già con il saluto, "
        "la aggiunge Gmail. Se un dato non ce l'hai, dillo e indica il canale giusto invece di "
        "inventarlo.\n"
        "LINGUA: rispondi nella STESSA lingua in cui è scritta l'email del cliente, "
        "qualunque sia. Queste istruzioni sono in italiano solo per uso interno.\n"
        f"CANALE: il cliente ci ha GIÀ scritto a {settings.mail_from_address} e questa è "
        f"la risposta da lì. NON dire MAI di scrivere a {settings.mail_from_address}: "
        "dove le regole dicono di scrivere a quell'indirizzo, in email vuol dire "
        "rispondere a QUESTA email (es. accrediti: \"rispondi a questa email con i "
        "dettagli\"). Se serve una verifica dello staff, scrivi che lo staff "
        "verifica e ricontatta il cliente in questa conversazione. Gli ALTRI recapiti "
        "restano validi: rimborsi@gatemilano.com per i rimborsi, marketing@ per i "
        "biglietti non ricevuti."
    )
    # La lingua si rileva sull'email del cliente, non su user_message: le istruzioni
    # qui sopra sono in italiano e falserebbero il rilevamento.
    return await generate_response(
        venue=venue, user_message=user_message, rag_context=rag_context, history=[],
        language_text=_email_text(msg),
    )


async def _notify(msg: dict, verdict: dict, status: str, reply: str = "",
                  reason: str = "", draft_id: str = "") -> None:
    """Email su Discord, come le chat IG/WA: lo staff la vede e la invia da lì."""
    try:
        from notifications.discord import notify_email
        await notify_email(msg, verdict, _venue_for(msg), reply, status,
                           reason=reason, draft_id=draft_id)
    except Exception:
        logger.debug("Notifica Discord non riuscita (non blocca il flusso)", exc_info=True)


async def _can_send_as() -> bool:
    try:
        return await gm.can_send_as(settings.mail_reply_as)
    except Exception as e:
        logger.warning("Verifica alias 'Invia come' fallita (→ bozza): %s", e)
        return False


async def process_one(message_id: str, followup: bool = False, prior: str = "") -> str:
    """Gestisce una email. Ritorna l'esito, per i log. Non solleva mai."""
    try:
        msg = await gm.get_message(message_id)
    except Exception as e:
        logger.error("Lettura email %s fallita: %s", message_id, e)
        return "errore-lettura"

    # followup: risposta del cliente (a george@) in una conversazione del bot.
    skip = triage.hard_skip(msg, box=settings.mail_reply_as if followup else None)
    if skip:
        await gm.add_label(message_id, LABEL_SKIPPED)
        return f"ignorata ({skip})"

    # Se nel thread abbiamo già scritto, non ci torniamo sopra: è la guardia che
    # impedisce sia i loop sia di scavalcare una risposta data a mano dallo staff.
    try:
        if not followup and await gm.thread_has_our_reply(msg["thread_id"]):
            await gm.add_label(message_id, LABEL_SKIPPED)
            return "ignorata (thread già gestito)"
    except Exception as e:
        logger.warning("Controllo thread fallito su %s (→ ignoro): %s", message_id, e)
        return "errore-thread"

    verdict = await triage.classify(msg)

    # Proposte (artisti, agenzie, portali, servizi): risposta su misura scritta dal
    # modello e controllata; se il controllo non passa, il testo fisso approvato.
    if verdict.get("categoria") in triage.PROPOSAL_CATEGORIES and not followup:
        from mail.proposals import write_reply
        reply, origin = await write_reply(
            msg, commercial=verdict.get("categoria") == triage.COMMERCIAL_CATEGORY)
        ok, reason = triage.can_send_proposal_reply(msg, verdict)
        if ok and not await _can_send_as():
            ok, reason = False, (f"{settings.mail_reply_as} non è un indirizzo "
                                 "'Invia come' verificato in Gmail")
        if ok:
            await gm.send_reply(msg, reply, label=gm.BOT_SENT_LABEL)
            await gm.add_label(message_id, LABEL_DONE)
            await _notify(msg, verdict, "inviata", reply=reply,
                          reason="" if origin == "generata" else origin)
            return "inviata (proposta)"
        draft_id = await gm.create_draft(msg, reply)
        await gm.add_label(message_id, LABEL_REVIEW)
        await _notify(msg, verdict, "bozza", reply=reply, reason=reason, draft_id=draft_id)
        return f"bozza ({reason})"

    try:
        body = await _compose(msg, verdict, prior=prior)
    except Exception as e:
        logger.error("Generazione risposta fallita per %s: %s", message_id, e)
        await gm.add_label(message_id, LABEL_REVIEW)
        await _notify(msg, verdict, "revisione", reason="generazione della risposta fallita")
        return "errore-generazione"

    if not body or "non riesco a rispondere" in body.lower():
        await gm.add_label(message_id, LABEL_REVIEW)
        await _notify(msg, verdict, "revisione", reason="il bot non ha saputo rispondere")
        return "revisione (nessuna risposta utile)"

    ok, reason = triage.can_autosend(msg, verdict)
    if followup:
        # Il seguito di una conversazione lo rilegge sempre una persona.
        ok, reason = False, "risposta del cliente a una nostra mail: rileggi prima di inviare"
    # Rete di sicurezza: rimandare a info@ chi ha appena scritto a info@ è una
    # risposta inutile. Se il modello lo fa comunque, la si fa rileggere.
    if ok and settings.mail_from_address.lower() in body.lower():
        ok, reason = False, f"la risposta rimanda a {settings.mail_from_address}"
    if ok and not await _can_send_as():
        ok, reason = False, (f"{settings.mail_reply_as} non è un indirizzo "
                             "'Invia come' verificato in Gmail")
    if ok:
        await gm.send_reply(msg, body + _footer(msg), label=gm.BOT_SENT_LABEL)
        await gm.add_label(message_id, LABEL_DONE)
        await _notify(msg, verdict, "inviata", reply=body)
        return "inviata"

    draft_id = await gm.create_draft(msg, body)
    await gm.add_label(message_id, LABEL_REVIEW)
    await _notify(msg, verdict, "bozza", reply=body, reason=reason, draft_id=draft_id)
    return f"bozza ({reason})"


async def run_once() -> dict:
    """Un giro del risponditore. È la funzione schedulata."""
    if not settings.mail_autoresponder_enabled:
        return {"skipped": "disattivato"}
    if not gm.configured():
        logger.warning("Risponditore email attivo ma credenziali Gmail mancanti")
        return {"skipped": "credenziali mancanti"}

    try:
        ids = await gm.list_unprocessed(_query(), settings.mail_max_per_run)
    except Exception as e:
        logger.error("Lettura casella fallita: %s", e)
        return {"error": str(e)[:200]}

    esiti: dict[str, int] = {}
    for mid in ids:
        esito = await process_one(mid)
        key = esito.split(" (")[0]
        esiti[key] = esiti.get(key, 0) + 1
    if ids:
        logger.info("Risponditore email: %d messaggi — %s", len(ids), esiti)
    return {"processate": len(ids), "esiti": esiti}
