"""Triage delle email: cosa può partire da solo e cosa deve passare da una persona.

La regola di fondo è una sola: **nel dubbio, bozza**. Ogni volta che qualcosa non
torna — categoria delicata, mittente strano, allegati, bassa confidenza — si prepara
la risposta ma non si manda.

Le categorie che possono partire da sole sono quelle in cui la risposta è interamente
derivabile da dati che abbiamo e verifichiamo (calendario eventi, disponibilità tavoli
dal sito, policy scritte in knowledge base). Tutto ciò che implica una valutazione, un
impegno economico o una persona in difficoltà resta in mano allo staff.
"""
from __future__ import annotations

import logging
import re

from ai.claude_client import _client, compat_kwargs, first_text
from config import settings

logger = logging.getLogger(__name__)

# Categorie in cui la risposta è fattuale e verificabile: può partire da sola.
AUTOSEND_CATEGORIES = {
    "orari",            # quando aprite, fino a che ora
    "biglietti",        # prezzi, dove si comprano, link
    "tavoli",           # prezzi, cosa include il minimo, link di prenotazione
    "eta",              # età minima della serata, documento
    "dress_code",
    "come_arrivare",    # indirizzo, mezzi, parcheggio
    "programma",        # che serata c'è il giorno X, line-up
}

# Categorie che preparano SEMPRE una bozza, per quanto il bot sembri sicuro.
REVIEW_CATEGORIES = {
    "accrediti",        # stampa, creator, artisti: è una valutazione dello staff
    "accessibilita",    # disabilità: delicata, e dipende anche dalla venue ospitante
    "rimborsi",         # soldi
    "reclami",          # un cliente arrabbiato non lo gestisce un automatismo
    "lavoro",           # candidature, CV
    "fornitori",        # fatture, amministrazione
    "collaborazioni",   # partnership, uffici stampa, agenzie
    "privati",          # eventi privati, affitto sala: si tratta
    "altro",
}

# Parole che da sole bastano a mandare in revisione, qualunque sia la categoria.
# Non è una classificazione, è una rete di sicurezza.
_RED_FLAGS = re.compile(
    r"\b(avvocat\w+|legale|diffid\w+|denunc\w+|risarcim\w+|rimbors\w+|"
    r"fattur\w+|iva|bonifico|pagament\w+ non|contest\w+|"
    r"l\.?\s?104|invalidit\w+|disabil\w+|carrozzin\w+|"
    r"minorenn\w+|polizia|carabinier\w+|ambulanz\w+|infortun\w+)\b",
    re.IGNORECASE,
)

# Mittenti a cui non si risponde MAI: automatismi, liste, noi stessi.
_NEVER_REPLY = re.compile(
    r"(no[-_.]?reply|noreply|donotreply|mailer-daemon|postmaster|bounce|"
    r"notification|notifiche|newsletter|automated|automatic)",
    re.IGNORECASE,
)
_OWN_DOMAINS = ("gatemilano.com", "gatesardinia.it", "gatemilano.it")

_CLASSIFIER_SYSTEM = """Classifichi le email in arrivo alla casella di un locale notturno.
Rispondi SOLO chiamando lo strumento, senza testo.

Categorie:
- orari: orari di apertura e chiusura
- biglietti: prezzi, dove comprare, prevendite, link
- tavoli: tavoli VIP, minimo di spesa, bottiglie, prenotazione tavolo
- eta: età minima, ingresso minorenni, documenti
- dress_code: abbigliamento, cosa si può indossare
- come_arrivare: indirizzo, mezzi, parcheggio, navette
- programma: che serata c'è in una data, line-up, artisti
- accrediti: richieste di omaggi, stampa, creator, artisti, guestlist
- accessibilita: disabilità, carrozzina, accompagnatore
- rimborsi: soldi indietro, biglietti per serate annullate
- reclami: lamentele, disservizi
- lavoro: candidature, CV, "cerco lavoro"
- fornitori: fatture, amministrazione, pagamenti
- collaborazioni: partnership, agenzie, uffici stampa, proposte commerciali
- privati: eventi privati, compleanni organizzati, affitto spazio
- altro: tutto ciò che non rientra sopra, o se non sei sicuro

confidenza: "alta" solo se la categoria è evidente dal testo. Nel dubbio "bassa"."""

_TOOL = {
    "name": "classifica_email",
    "description": "Registra la categoria dell'email.",
    "input_schema": {
        "type": "object",
        "properties": {
            "categoria": {"type": "string"},
            "confidenza": {"type": "string", "enum": ["alta", "bassa"]},
            "domanda": {
                "type": "string",
                "description": "La richiesta del cliente in una frase, come la porresti al bot.",
            },
        },
        "required": ["categoria", "confidenza", "domanda"],
    },
}


def hard_skip(msg: dict) -> str:
    """Motivo per cui questa email non va proprio toccata, o "" se si può procedere."""
    sender = msg.get("from_email", "")
    if not sender:
        return "mittente assente"
    if _NEVER_REPLY.search(sender) or _NEVER_REPLY.search(msg.get("from", "")):
        return "mittente automatico"
    if any(sender.endswith("@" + d) for d in _OWN_DOMAINS):
        return "mittente interno"
    if msg.get("list_unsubscribe") or msg.get("precedence", "").lower() in ("bulk", "list", "junk"):
        return "newsletter/lista"
    if msg.get("auto_submitted", "").lower() not in ("", "no"):
        return "messaggio auto-generato"
    if not (msg.get("body") or "").strip():
        return "corpo vuoto"
    return ""


async def classify(msg: dict) -> dict:
    """{categoria, confidenza, domanda}. Su errore ricade su 'altro' → bozza."""
    model = settings.venue_classifier_model or settings.model
    user = f"Oggetto: {msg.get('subject','')}\n\n{(msg.get('body') or '')[:3000]}"
    try:
        resp = await _client.messages.create(
            model=model,
            max_tokens=300,
            system=_CLASSIFIER_SYSTEM,
            tools=[_TOOL],
            tool_choice={"type": "tool", "name": "classifica_email"},
            messages=[{"role": "user", "content": user}],
            **compat_kwargs(model, 0),
        )
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                out = dict(block.input or {})
                out.setdefault("categoria", "altro")
                out.setdefault("confidenza", "bassa")
                out.setdefault("domanda", msg.get("subject", ""))
                return out
    except Exception as e:
        logger.warning("Classificazione email fallita (→ revisione): %s", e)
    return {"categoria": "altro", "confidenza": "bassa",
            "domanda": msg.get("subject", "")}


def can_autosend(msg: dict, verdict: dict) -> tuple[bool, str]:
    """(si può inviare da soli?, motivo se no)."""
    if settings.mail_draft_only:
        return False, "modalità solo-bozze attiva"
    if verdict.get("categoria") not in AUTOSEND_CATEGORIES:
        return False, f"categoria '{verdict.get('categoria')}' da rivedere"
    if verdict.get("confidenza") != "alta":
        return False, "classificazione incerta"
    if msg.get("has_attachments"):
        return False, "ci sono allegati"
    text = f"{msg.get('subject','')} {msg.get('body','')}"
    found = _RED_FLAGS.search(text)
    if found:
        return False, f"contiene '{found.group(0)}'"
    return True, ""
