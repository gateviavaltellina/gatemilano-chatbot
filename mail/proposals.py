"""Risposte alle proposte (artisti, agenzie, portali, servizi) scritte dal modello.

Lo staff (13/10): "deve sapersi comportare". Al posto di un testo fisso uguale per
tutti, il modello legge la proposta e risponde su misura: riconosce cosa viene
proposto, ringrazia, resta professionale e distaccato, non si impegna a nulla.

Queste risposte partono da sole, quindi prima dell'invio passano un controllo:
niente impegni ("siamo interessati", "fissiamo una call"), niente prezzi, link,
telefoni o indirizzi email, lunghezza contenuta. Se il controllo non passa, o il
modello non risponde, si usa il testo fisso approvato (mail/templates.py).
"""
from __future__ import annotations

import logging
import re

from ai.claude_client import _client, compat_kwargs, first_text
from config import settings
from mail.templates import proposal_reply

logger = logging.getLogger(__name__)

_SYSTEM = """Sei lo staff di Gate Milano, club di Milano. Rispondi a un'email che propone
qualcosa al locale: un artista, un DJ, una band, una data (agenzie di booking) oppure un
servizio, un portale, pubblicità, software, una partnership.

Come rispondere:
- nella STESSA lingua dell'email;
- breve: 3-5 frasi in tutto, saluto iniziale compreso;
- mostra di aver letto: richiama in poche parole cosa propongono (l'artista, il
  portale, il servizio), senza lodi esagerate;
- tono cortese, professionale ma distaccato: ringrazi e dici che valutate con
  attenzione le proposte che ricevete e che, se ci sarà interesse, sarete voi a
  ricontattarli. Per artisti e agenzie puoi dire che lavorate costantemente alla
  programmazione del Gate;
- NON prendere impegni: niente "siamo interessati", niente call, incontri,
  appuntamenti, niente richieste di materiale, preventivi o listini;
- NON scrivere prezzi, cifre, link, numeri di telefono o indirizzi email;
- niente formula di chiusura né firma ("Un saluto", "Best", nomi): la firma la
  aggiunge Gmail.

Scrivi SOLO il testo della risposta."""

# Cose che una risposta di cortesia, inviata senza revisione, non deve mai contenere.
_FORBIDDEN = re.compile(
    r"(https?://|www\.|@|\+?\d[\d\s.-]{6,}\d|€|\$|\beur\b|\bfee\b|\bbudget\b|"
    r"preventiv|listino|\bcall\b|chiamat|videochiam|appuntament|\bmeeting\b|"
    r"siamo interessat|ci interessa|ci piacerebbe|accettiamo|confermiamo|"
    r"we are interested|we'd love|we would love|let's|schedule|"
    r"send us|inviateci|mandateci|mandaci|inviaci)",
    re.IGNORECASE,
)
_MAX_CHARS = 900


def check_reply(text: str) -> str:
    """Motivo per cui la risposta non può partire da sola, o "" se va bene."""
    t = (text or "").strip()
    if len(t) < 40:
        return "risposta vuota o troppo corta"
    if len(t) > _MAX_CHARS:
        return "risposta troppo lunga"
    m = _FORBIDDEN.search(t)
    if m:
        return f"contiene '{m.group(0).strip()}'"
    return ""


async def write_reply(msg: dict, commercial: bool) -> tuple[str, str]:
    """(testo, origine): "generata" se il modello ha scritto una risposta valida,
    altrimenti il testo fisso approvato con il motivo ("fissa: …")."""
    email = f"Oggetto: {msg.get('subject','')}\n\n{(msg.get('body') or '')[:4000]}"
    fallback = proposal_reply(f"{msg.get('subject','')}\n{msg.get('body','')}", commercial=commercial)
    try:
        model = settings.model
        resp = await _client.messages.create(
            model=model, max_tokens=500, system=_SYSTEM,
            messages=[{"role": "user", "content": email}],
            **compat_kwargs(model, None),
        )
        text = first_text(resp).strip()
    except Exception as e:
        logger.warning("Risposta alla proposta non generata (→ testo fisso): %s", e)
        return fallback, "fissa: generazione non riuscita"
    problem = check_reply(text)
    if problem:
        logger.info("Risposta generata scartata (%s) → testo fisso", problem)
        return fallback, f"fissa: {problem}"
    return text, "generata"
