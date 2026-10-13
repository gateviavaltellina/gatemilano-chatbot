"""Risposte fisse, approvate dallo staff: non generate, quindi possono partire da sole.

Proposte B2B (13/10): "lavoriamo costantemente alla programmazione del Gate, se siamo
interessati ti scriviamo" — in maniera carina, professionale ma fredda, nella lingua di
chi scrive. Niente promesse, niente richieste di altro materiale.
"""
from __future__ import annotations

from ai.language import detect_language

_PROPOSAL = {
    "italiano": (
        "Buongiorno,\n\n"
        "grazie per averci scritto e per la proposta.\n\n"
        "Lavoriamo costantemente alla programmazione del Gate e valutiamo con attenzione "
        "tutte le proposte che riceviamo. Se dovesse esserci interesse, sarà nostra cura "
        "ricontattarvi.\n\n"
        "Cordiali saluti,\nGate Milano"
    ),
    "inglese": (
        "Hello,\n\n"
        "thank you for reaching out and for your proposal.\n\n"
        "We are constantly working on Gate's programming and we carefully review every "
        "proposal we receive. Should there be any interest, we will get back to you.\n\n"
        "Kind regards,\nGate Milano"
    ),
    "spagnolo": (
        "Hola,\n\n"
        "gracias por escribirnos y por la propuesta.\n\n"
        "Trabajamos constantemente en la programación del Gate y valoramos con atención "
        "todas las propuestas que recibimos. Si hubiera interés, nos pondremos en "
        "contacto con vosotros.\n\n"
        "Un saludo,\nGate Milano"
    ),
    "francese": (
        "Bonjour,\n\n"
        "merci de nous avoir écrit et pour votre proposition.\n\n"
        "Nous travaillons en permanence sur la programmation du Gate et nous étudions avec "
        "attention toutes les propositions que nous recevons. En cas d'intérêt, nous "
        "reviendrons vers vous.\n\n"
        "Cordialement,\nGate Milano"
    ),
    "tedesco": (
        "Hallo,\n\n"
        "vielen Dank für Ihre Nachricht und Ihren Vorschlag.\n\n"
        "Wir arbeiten laufend an der Programmgestaltung des Gate und prüfen alle "
        "Vorschläge, die wir erhalten, sorgfältig. Bei Interesse melden wir uns bei "
        "Ihnen.\n\n"
        "Mit freundlichen Grüßen\nGate Milano"
    ),
}


def proposal_reply(text: str) -> str:
    """Risposta alle proposte nella lingua del testo. Lingua incerta: italiano + inglese."""
    lang = detect_language(text)
    if lang in _PROPOSAL:
        return _PROPOSAL[lang]
    return _PROPOSAL["italiano"] + "\n\n— — —\n\n" + _PROPOSAL["inglese"]
