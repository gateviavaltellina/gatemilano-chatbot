"""Risposte fisse, approvate dallo staff: non generate, quindi possono partire da sole.

Proposte B2B (13/10): "lavoriamo costantemente alla programmazione del Gate, se siamo
interessati ti scriviamo" — in maniera carina, professionale ma fredda, nella lingua di
chi scrive. Niente promesse, niente richieste di altro materiale. Niente firma né
formula di chiusura: la aggiunge gmail_client dalla firma Gmail di george@, che apre
già con "Best," (un "Un saluto" qui risulterebbe doppio).
"""
from __future__ import annotations

from ai.language import detect_language

_PROPOSAL = {
    "italiano": (
        "Buongiorno,\n\n"
        "grazie per averci scritto e per la proposta.\n\n"
        "Lavoriamo costantemente alla programmazione del Gate e valutiamo con attenzione "
        "tutte le proposte che riceviamo. Se dovesse esserci interesse, sarà nostra cura "
        "ricontattarvi."
    ),
    "inglese": (
        "Hello,\n\n"
        "thank you for reaching out and for your proposal.\n\n"
        "We are constantly working on Gate's programming and we carefully review every "
        "proposal we receive. Should there be any interest, we will get back to you."
    ),
    "spagnolo": (
        "Hola,\n\n"
        "gracias por escribirnos y por la propuesta.\n\n"
        "Trabajamos constantemente en la programación del Gate y valoramos con atención "
        "todas las propuestas que recibimos. Si hubiera interés, nos pondremos en "
        "contacto con vosotros."
    ),
    "francese": (
        "Bonjour,\n\n"
        "merci de nous avoir écrit et pour votre proposition.\n\n"
        "Nous travaillons en permanence sur la programmation du Gate et nous étudions avec "
        "attention toutes les propositions que nous recevons. En cas d'intérêt, nous "
        "reviendrons vers vous."
    ),
    "tedesco": (
        "Hallo,\n\n"
        "vielen Dank für Ihre Nachricht und Ihren Vorschlag.\n\n"
        "Wir arbeiten laufend an der Programmgestaltung des Gate und prüfen alle "
        "Vorschläge, die wir erhalten, sorgfältig. Bei Interesse melden wir uns bei "
        "Ihnen."
    ),
}


def proposal_reply(text: str) -> str:
    """Risposta alle proposte nella lingua del testo. Lingua incerta: italiano + inglese."""
    lang = detect_language(text)
    if lang in _PROPOSAL:
        return _PROPOSAL[lang]
    return _PROPOSAL["italiano"] + "\n\n— — —\n\n" + _PROPOSAL["inglese"]


# --- rimborsi (rimborsi@) -----------------------------------------------------
# Policy staff 13/10: rimborso SOLO a chi, con biglietto valido, è stato respinto
# all'ingresso; servono prova d'acquisto, biglietti e motivazione; importo pieno.

_REFUND_ITEMS = {
    "prova_acquisto": ("la prova d'acquisto (la conferma o la ricevuta del pagamento)",
                       "the proof of purchase (the payment confirmation or receipt)"),
    "biglietti": ("i biglietti (il PDF o lo screenshot con il QR code)",
                  "the tickets (the PDF or a screenshot showing the QR code)"),
    "motivazione": ("cosa è successo all'ingresso: il motivo per cui non siete potuti entrare",
                    "what happened at the entrance: why you were not allowed in"),
}


def _en(lang: str) -> bool:
    return (lang or "").lower() not in ("", "it", "italiano", "italian")


def refund_missing(lang: str, missing: list[str]) -> str:
    i = 1 if _en(lang) else 0
    items = "\n".join(f"- {_REFUND_ITEMS[m][i]}" for m in missing if m in _REFUND_ITEMS)
    if i:
        return ("Hello,\n\nwe have received your refund request. To process it we still need:\n"
                f"{items}\n\nJust reply to this email with what is missing.")
    return ("Ciao,\n\nabbiamo ricevuto la tua richiesta di rimborso. Per poterla prendere in "
            f"carico ci servono ancora:\n{items}\n\nRispondi pure a questa email con quanto "
            "indicato.")


def refund_ack(lang: str) -> str:
    if _en(lang):
        return ("Hello,\n\nwe have received your complete refund request. We are checking it "
                "and will update you by replying to this email: there is no need to send it "
                "again.")
    return ("Ciao,\n\nabbiamo ricevuto la tua richiesta di rimborso completa. La stiamo "
            "verificando e ti aggiorneremo rispondendo a questa email: non serve inviarla di "
            "nuovo.")


def refund_done(lang: str, event: str = "") -> str:
    ev_it = f" per {event}" if event else ""
    ev_en = f" for {event}" if event else ""
    if _en(lang):
        return (f"Hello,\n\nthe refund of your tickets{ev_en} has been issued for the full "
                "amount paid, fees included. It will be credited to the payment method used "
                "for the purchase: timing depends on your bank.")
    return (f"Ciao,\n\nil rimborso dei tuoi biglietti{ev_it} è stato effettuato per l'intero "
            "importo pagato, commissioni comprese. L'accredito arriva sullo stesso metodo di "
            "pagamento usato per l'acquisto: i tempi dipendono dalla tua banca.")


def refund_refused(lang: str, note: str = "") -> str:
    if _en(lang):
        base = ("Hello,\n\nwe have checked your request. Unfortunately it does not fall within "
                "the refundable cases: a refund is only issued to those who, holding a valid "
                "ticket, were not allowed in at the entrance.")
        return base + (f"\n\n{note}" if note else "") + ""
    base = ("Ciao,\n\nabbiamo verificato la tua richiesta. Purtroppo non rientra nei casi "
            "rimborsabili: il rimborso è previsto solo per chi, con un biglietto valido, non è "
            "stato fatto entrare all'ingresso.")
    return base + (f"\n\n{note}" if note else "") + ""
