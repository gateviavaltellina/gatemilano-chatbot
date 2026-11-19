"""Sanitizzazione della history prima della chiamata API.

Scartiamo contenuti malformati senza perdere richieste ancora senza risposta.
L'API accetta turni user consecutivi: non serve cancellare il precedente.
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


def test_preserves_trailing_user():
    # Il messaggio ancora senza risposta va unito al prossimo, mai cancellato.
    h = [
        {"role": "user", "content": "ciao"},
        {"role": "assistant", "content": "Ciao!"},
        {"role": "user", "content": "resto in sospeso"},
    ]
    out = _sanitize_history(h)
    assert out[-1] == {"role": "user", "content": "resto in sospeso"}


async def test_pending_user_selection_reaches_model_with_next_message(monkeypatch):
    from types import SimpleNamespace
    import ai.claude_client as cc

    captured = {}

    async def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1, output_tokens=1),
            content=[SimpleNamespace(type="text", text="ok")])

    monkeypatch.setattr(cc, "_client", SimpleNamespace(messages=SimpleNamespace(create=create)))
    original = [{"role": "user", "content": "preferisco BLK"}]
    await cc.generate_response("gate_milano", "siamo in cinque", "", original)
    messages = captured["messages"]
    assert len(messages) == 2
    assert messages[0]["role"] == messages[1]["role"] == "user"
    assert "preferisco BLK" in messages[0]["content"][0]["text"]
    assert "siamo in cinque" in messages[1]["content"]
    assert original == [{"role": "user", "content": "preferisco BLK"}]


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
