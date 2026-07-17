"""Sanitizzazione della history prima della chiamata API.

Una history malformata (contenuto vuoto o ruoli non alternati) fa tornare
all'API Anthropic un 400 non ritentabile → il cliente riceve "non riesco a
rispondere". _sanitize_history previene questa classe di errori.
"""
from ai.claude_client import _sanitize_history


def test_drops_empty_content():
    h = [
        {"role": "user", "content": "ciao"},
        {"role": "assistant", "content": "   "},   # vuoto → scartato
        {"role": "assistant", "content": "Ciao!"},
    ]
    out = _sanitize_history(h)
    assert out == [
        {"role": "user", "content": "ciao"},
        {"role": "assistant", "content": "Ciao!"},
    ]


def test_merges_consecutive_same_role():
    h = [
        {"role": "user", "content": "ciao"},
        {"role": "user", "content": "ci sei?"},   # due user di fila → fusi
        {"role": "assistant", "content": "Sì!"},
    ]
    out = _sanitize_history(h)
    assert out == [
        {"role": "user", "content": "ciao\nci sei?"},
        {"role": "assistant", "content": "Sì!"},
    ]


def test_drops_trailing_user():
    # la history non deve finire con user: aggiungiamo noi il nuovo user dopo
    h = [
        {"role": "user", "content": "ciao"},
        {"role": "assistant", "content": "Ciao!"},
        {"role": "user", "content": "resto in sospeso"},
    ]
    out = _sanitize_history(h)
    assert out[-1]["role"] == "assistant"


def test_ignores_malformed_entries():
    h = [
        {"role": "system", "content": "x"},        # ruolo non valido → scartato
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": None},      # content non stringa → scartato
        {"role": "assistant", "content": "risposta"},
    ]
    out = _sanitize_history(h)
    assert out == [
        {"role": "user", "content": "ok"},
        {"role": "assistant", "content": "risposta"},
    ]


def test_empty_and_none_safe():
    assert _sanitize_history([]) == []
    assert _sanitize_history(None) == []
