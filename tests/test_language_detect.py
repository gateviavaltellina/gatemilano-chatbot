"""Lingua della risposta: il bot deve rispondere nella lingua del cliente.

Caso reale (Instagram, 5/9): un cliente chiede in inglese se il set di Kobosil dura due
ore e riceve la risposta in italiano; ripete la domanda in inglese ("I'm not asking about
the timetable, but about the duration of Kobosil's set") e riceve di nuovo italiano.

La "REGOLA PRIORITARIA" sulla lingua era già nel prompt, ma è una riga in fondo a ~25.000
token di knowledge base in italiano e veniva rispettata in modo incostante — soprattutto
quando la risposta corrispondeva a una "risposta tipo" già scritta in italiano nella KB.
Qui si testa il rilevatore che genera l'istruzione esplicita per messaggio.
"""
import pytest

from ai.language import detect_language, language_directive


@pytest.mark.parametrize("text,expected", [
    ("will the kobosil set be 2 hours long?", "inglese"),
    ("I'm not asking about the timetable, but about the duration of Kobosil's set.", "inglese"),
    ("does the backstage ticket include a drink", "inglese"),
    ("what time do you open on friday?", "inglese"),
    ("a que hora toca kobosil? quiero comprar entradas", "spagnolo"),
    ("bonjour, a quelle heure ouvrez vous vendredi?", "francese"),
    ("hallo, wann oeffnet ihr am freitag? ich moechte tickets kaufen", "tedesco"),
])
def test_lingue_riconosciute(text, expected):
    assert detect_language(text) == expected


@pytest.mark.parametrize("text", [
    "a che ora suona kobosil?",
    "mi dite i prezzi dei tavoli per sabato?",
    "vorrei prenotare un tavolo per sabato sera",
    "quanto costa l'ingresso venerdi?",
])
def test_italiano_nessuna_istruzione(text):
    """Sull'italiano il rilevatore non deve produrre nulla.

    Che risolva "italiano" o resti incerto è indifferente: conta che NON aggiunga
    istruzioni (il default del prompt è già l'italiano) e soprattutto che non scambi
    l'italiano per un'altra lingua.
    """
    assert detect_language(text) in (None, "italiano")
    assert language_directive(text) == ""


@pytest.mark.parametrize("text", [
    "ok",
    "?",
    "??",
    "grazie",
    "👍",
    "5",
    "si",
    "Kobosil",
    "Perreo XL",
])
def test_messaggi_troppo_corti_non_forzano_nulla(text):
    # Su un messaggio ambiguo forzare la lingua è peggio del comportamento attuale:
    # il rilevatore deve tacere e lasciare decidere al prompt.
    assert detect_language(text) is None
    assert language_directive(text) == ""


def test_italiano_non_genera_istruzione():
    # L'italiano è il default: nessuna riga aggiuntiva, nessun token sprecato.
    assert language_directive("a che ora aprite sabato sera?") == ""


def test_istruzione_nomina_la_lingua():
    d = language_directive("what time do you open on friday?")
    assert "INGLESE" in d
    assert "NON copiarle in italiano" in d


def test_gergo_inglese_non_inganna():
    # Il nostro gergo è pieno di inglese ("Main Room", "Backstage Ticket", "fast lane"):
    # dentro una frase italiana NON deve mai far rispondere in inglese.
    for t in ("il backstage ticket per la main room quanto costa?",
              "come funziona il fast lane del backstage ticket?",
              "il perreo xl di sabato ha ancora tavoli in main room?"):
        assert detect_language(t) != "inglese"
        assert language_directive(t) == ""


# --- caso reale WhatsApp 16/10: chat in inglese, risposta sulle bottiglie in italiano ---

from ai.language import language_reminder, resolve_language


def test_messaggio_corto_eredita_la_lingua_della_chat():
    history = [{"role": "user", "content": "Hi, I'd like to book a table for Saturday please"},
               {"role": "assistant", "content": "Sure! Here are the tables..."}]
    assert resolve_language("2 pls", history) == "inglese"


def test_messaggio_corto_italiano_non_eredita():
    history = [{"role": "user", "content": "Hi, I'd like to book a table for Saturday please"}]
    assert resolve_language("ok grazie", history) is None


def test_messaggio_chiaro_vince_sulla_chat():
    history = [{"role": "user", "content": "Hi, I'd like to book a table for Saturday please"}]
    assert resolve_language("vorrei sapere quanto costa il tavolo", history) == "italiano"


def test_promemoria_nella_lingua_del_cliente():
    assert language_reminder("inglese") == "Reply in English only."
    assert language_reminder("italiano") == ""
    assert language_reminder(None) == ""


async def test_promemoria_in_coda_al_messaggio_del_cliente(monkeypatch):
    """La riga di sistema non basta con una cronologia lunga: il promemoria va subito
    dopo il messaggio del cliente, che è l'ultima cosa che il modello legge."""
    import ai.claude_client as cc
    preso = {}

    class _Resp:
        content = [type("B", (), {"type": "text", "text": "Hi! The €300 table..."})()]
        usage = None
        stop_reason = "end_turn"

    async def _create(**kw):
        preso.update(kw)
        return _Resp()

    monkeypatch.setattr(cc._client.messages, "create", _create)
    history = [{"role": "user", "content": "do you have tables for saturday?"},
               {"role": "assistant", "content": "Yes! F1: https://booking..." * 30}]
    await cc.generate_response(venue="gate_milano",
                               user_message="2 people, and is there ant bottles included",
                               rag_context="", history=history)
    last = preso["messages"][-1]["content"]
    text = last if isinstance(last, str) else " ".join(b.get("text", "") for b in last)
    assert text.rstrip().endswith("[Reply in English only.]")
    assert "INGLESE" in preso["system"][-1]["text"]


async def test_nessun_promemoria_per_l_italiano(monkeypatch):
    import ai.claude_client as cc
    preso = {}

    class _Resp:
        content = [type("B", (), {"type": "text", "text": "Ciao!"})()]
        usage = None
        stop_reason = "end_turn"

    async def _create(**kw):
        preso.update(kw)
        return _Resp()

    monkeypatch.setattr(cc._client.messages, "create", _create)
    await cc.generate_response(venue="gate_milano", user_message="quanto costa il tavolo sabato?",
                               rag_context="", history=[])
    assert "Reply in" not in str(preso["messages"][-1]["content"])
