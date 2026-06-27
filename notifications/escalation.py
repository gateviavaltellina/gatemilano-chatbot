"""Rilevamento temi sensibili nei messaggi utente → alert staff.

Quando un messaggio tocca accessibilità, rimborsi, salute/emergenze o reclami,
lo staff va allertato su Discord IN PARALLELO alla risposta del bot: questi sono
i casi dove un errore del bot costa di più (vedi il caso reale "sedia a rotelle /
Carl Cox" e l'audit anti-allucinazione). Il bot continua a rispondere, ma lo
staff vede subito un alert prominente e può prendere in carico con !t / !r.
"""

# categoria (con emoji per l'alert) → keyword in minuscolo (match per sottostringa)
_CATEGORIES = {
    # GAP wh-41: "I wish I might not be alive on Friday" non generava alcun alert.
    # L'autolesionismo è la categoria a priorità più alta: lo staff va allertato
    # SEMPRE, anche se il bot risponde in parallelo. Keyword volutamente larghe
    # (IT + EN) ma scelte per non colpire i messaggi benigni operativi.
    "🆘 Autolesionismo": [
        # inglese
        "kill myself", "hurt myself", "harm myself", "want to die", "wanna die",
        "end my life", "not be alive", "don't want to live", "dont want to live",
        "suicid", "self-harm", "self harm",
        # italiano
        "uccidermi", "uccidersi", "farla finita", "farmi del male", "autoles",
        "togliermi la vita", "non voglio più vivere", "non voglio piu vivere",
        "non voglio vivere", "voglio morire", "pensieri di morte",
    ],
    "♿ Accessibilità": [
        "accessibil", "carrozzin", "sedia a rotelle", "disabil", "invalid",
        "mobilità ridotta", "mobilita ridotta", "wheelchair", "deambul", "stampelle",
    ],
    "💸 Rimborso": [
        "rimbors", "refund", "soldi indietro", "restituz", "chargeback", "storno",
    ],
    "🚑 Salute/Emergenza": [
        "allerg", "intolleran", "emergenz", "malore", "ambulanz", "svenut",
        "epiless", "diabet", "incinta", "gravidanz", "celiac",
    ],
    "⚠️ Reclamo": [
        "reclamo", "complaint", "lament", "denuncia", "avvocat", "truffa",
        "rubato", "furto", "aggred", "rissa", "molest", "vergogn",
    ],
}


def detect_sensitive(text: str) -> list[str]:
    """Ritorna le categorie sensibili rilevate nel testo (lista vuota se nessuna)."""
    t = (text or "").lower()
    return [cat for cat, kws in _CATEGORIES.items() if any(k in t for k in kws)]
