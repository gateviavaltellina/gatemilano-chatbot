from notifications.discord import _conversation_context


def test_conversation_context_adds_venue_and_example():
    ctx = _conversation_context(None, "gate_milano", "domanda", "risposta sbagliata")
    assert ctx["venue"] == "gate_milano"
    assert ctx["user_msg"] == "domanda"
    assert ctx["bot_reply"] == "risposta sbagliata"


def test_conversation_context_preserves_existing_ig_context():
    ctx = _conversation_context(
        {"ig_account_id": "A", "sender_id": "S"}, "gate_milano", "d", "r"
    )
    assert ctx["ig_account_id"] == "A"
    assert ctx["sender_id"] == "S"
    assert ctx["venue"] == "gate_milano"
    assert ctx["bot_reply"] == "r"


def test_conversation_context_truncates_long_strings():
    long = "x" * 5000
    ctx = _conversation_context(None, "gate_milano", long, long)
    assert len(ctx["user_msg"]) == 1024
    assert len(ctx["bot_reply"]) == 1024
