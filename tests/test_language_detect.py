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
