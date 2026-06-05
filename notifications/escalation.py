"""Rilevamento temi sensibili nei messaggi utente → alert staff.

Quando un messaggio tocca accessibilità, rimborsi, salute/emergenze o reclami,
lo staff va allertato su Discord IN PARALLELO alla risposta del bot: questi sono
i casi dove un errore del bot costa di più (vedi il caso reale "sedia a rotelle /
Carl Cox" e l'audit anti-allucinazione). Il bot continua a rispondere, ma lo
staff vede subito un alert prominente e può prendere in carico con !t / !r.
"""

# categoria (con emoji per l'alert) → keyword in minuscolo (match per sottostringa)
_CATEGORIES = {
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
