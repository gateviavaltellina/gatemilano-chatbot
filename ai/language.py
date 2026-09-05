"""Rilevamento della lingua del messaggio cliente, per istruire il modello esplicitamente.

Perché serve. Il prompt contiene già una "REGOLA PRIORITARIA" sulla lingua, ma è una
riga in fondo a ~25.000 token di knowledge base in italiano: il modello la rispetta in
modo incostante, soprattutto quando la risposta corrisponde a una "risposta tipo" scritta
in italiano nella KB. Caso reale (Instagram, 5/9): un cliente chiede in inglese la durata
del set di Kobosil e riceve due risposte di fila in italiano, anche dopo aver ripetuto la
domanda in inglese.

Una regola generica non basta; una riga specifica ("il cliente ha scritto in INGLESE")
posta subito prima della risposta sì. Qui ricaviamo quella riga.

Il rilevatore è volutamente prudente: usa parole funzionali (articoli, preposizioni,
ausiliari) che distinguono bene le lingue con pochi token, e resta zitto quando non è
sicuro — su un messaggio ambiguo è meglio il comportamento attuale che forzare la lingua
sbagliata. "ok", "grazie", un emoji o un numero non attivano nulla.
"""
import re
import unicodedata

# Parole funzionali ad alta frequenza e basso rumore. Sono scelte per NON collidere fra
# lingue: niente parole che esistono uguali in italiano e spagnolo (es. "la", "no", "di").
_MARKERS: dict[str, set[str]] = {
    "inglese": {
        "the", "is", "are", "do", "does", "did", "can", "could", "would", "should",
        "what", "when", "where", "which", "who", "how", "why", "there", "have", "has",
        "you", "your", "yours", "i", "my", "we", "our", "they", "it", "its",
        "and", "or", "but", "for", "with", "without", "about", "from", "into",
        "please", "thanks", "thank", "hello", "hi", "hey", "yes", "sorry",
        "ticket", "tickets", "night", "table", "tables", "long", "much", "many",
        "open", "opening", "closing", "book", "booking", "buy", "price", "prices",
    },
    "spagnolo": {
        "que", "como", "cuando", "donde", "cual", "quien", "porque", "por", "para",
        "es", "son", "esta", "estan", "hay", "tiene", "tienen", "puedo", "puede",
        "quiero", "queria", "necesito", "gracias", "hola", "buenas", "usted",
        "entrada", "entradas", "precio", "precios", "cuanto", "cuantos", "mesa",
        "noche", "abren", "abre", "cierran", "comprar", "reservar", "horario",
    },
    "francese": {
        "est", "sont", "quoi", "quand", "ou", "comment", "pourquoi", "combien",
        "je", "tu", "vous", "nous", "ils", "elles", "mon", "votre", "notre",
        "les", "des", "une", "un", "avec", "sans", "pour", "dans", "sur",
        "bonjour", "merci", "salut", "billet", "billets", "prix", "soiree",
        "ouvrez", "ouvre", "reserver", "table", "puis", "peux", "pouvez",
    },
    "tedesco": {
        "ist", "sind", "was", "wann", "wo", "wie", "warum", "wieviel", "wieviele",
        "ich", "du", "sie", "wir", "ihr", "mein", "meine", "euer", "und", "oder",
        "aber", "mit", "ohne", "fuer", "von", "auf", "der", "die", "das", "den",
        "hallo", "danke", "bitte", "ticket", "tickets", "preis", "preise",
        "nacht", "tisch", "oeffnet", "geoeffnet", "kaufen", "reservieren",
    },
    "italiano": {
        "che", "cosa", "quando", "dove", "come", "perche", "quanto", "quanti",
        "sono", "siete", "posso", "potete", "vorrei", "voglio", "avete", "hai",
        "ciao", "grazie", "buonasera", "buongiorno", "salve", "scusa", "scusate",
        "biglietto", "biglietti", "prezzo", "prezzi", "serata", "tavolo", "tavoli",
        "aprite", "apre", "chiude", "chiudete", "ingresso", "prenotare", "comprare",
        "del", "della", "dello", "degli", "nel", "nella", "sul", "sulla", "col",
    },
}

# Minimi per parlare: sotto questa soglia il rilevatore tace e vale il comportamento
# normale del prompt. Un "ok" o un "?" non devono mai forzare una lingua.
_MIN_WORDS = 3
_MIN_HITS = 2
_MIN_MARGIN = 2  # la lingua vincente deve staccare la seconda di almeno tanto


def _normalize(text: str) -> list[str]:
    """Minuscolo, senza accenti, solo parole — così 'perché' e 'perche' contano uguale."""
    lowered = text.lower()
    stripped = "".join(
        c for c in unicodedata.normalize("NFD", lowered)
        if unicodedata.category(c) != "Mn"
    )
    return re.findall(r"[a-z']+", stripped)


def detect_language(text: str) -> str | None:
    """Nome ITALIANO della lingua del messaggio ('inglese', 'spagnolo', ...) o None.

    None significa "non sono sicuro": il chiamante non deve forzare nulla.
    """
    words = _normalize(text or "")
    if len(words) < _MIN_WORDS:
        return None
    unique = set(words)
    scores = {lang: len(unique & markers) for lang, markers in _MARKERS.items()}
    best = max(scores, key=lambda k: scores[k])
    best_score = scores[best]
    if best_score < _MIN_HITS:
        return None
    runner_up = max((v for k, v in scores.items() if k != best), default=0)
    if best_score - runner_up < _MIN_MARGIN:
        return None
    return best


def language_directive(text: str) -> str:
    """Riga da appendere in coda al blocco dinamico, o "" se la lingua non è certa.

    Va in fondo al prompt, subito prima del messaggio: è la posizione in cui il modello
    la rispetta davvero, mentre la stessa regola in mezzo alla knowledge base viene persa.
    """
    lang = detect_language(text)
    if not lang or lang == "italiano":
        return ""
    return (
        f"\n\n⚠️ LINGUA DI QUESTO MESSAGGIO: il cliente ha scritto in {lang.upper()}. "
        f"Rispondi in {lang}, per intero. Le informazioni qui sopra (knowledge base, "
        f"eventi, prezzi, 'risposte tipo') sono in italiano solo per uso interno: "
        f"riformulale in {lang}, NON copiarle in italiano e non mischiare le due lingue."
    )
