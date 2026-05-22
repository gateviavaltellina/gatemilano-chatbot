from ai.claude_client import build_system_blocks


def test_two_blocks_static_first_with_cache_control():
    blocks = build_system_blocks("gate_milano", "EVENTI: x", "lunedi 1 gennaio 2026, 12:00")
    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "REGOLE FONDAMENTALI" in blocks[0]["text"]
    assert "UPSELL PERREO" in blocks[0]["text"]  # sezione Perreo (Milano) sta nello statico
    assert "cache_control" not in blocks[1]


def test_dynamic_block_has_datetime_and_rag():
    blocks = build_system_blocks("gate_milano", "EVENTI: Perreo XL", "lunedi 1 gennaio 2026, 12:00")
    dyn = blocks[1]["text"]
    assert "EVENTI: Perreo XL" in dyn
    assert "lunedi 1 gennaio 2026" in dyn
    assert "DATA E ORA ATTUALE" in dyn


def test_static_block_has_no_dynamic_leak():
    # Invariante della cache: la parte statica NON deve contenere data/ora ne contesto
    # RAG, altrimenti il prefisso cambia a ogni messaggio e la cache non colpisce mai.
    blocks = build_system_blocks("gate_milano", "EVENTI_UNICI_XYZ", "martedi 2 gennaio 2026, 13:00")
    assert "EVENTI_UNICI_XYZ" not in blocks[0]["text"]
    assert "martedi 2 gennaio 2026" not in blocks[0]["text"]


def test_sardinia_static_has_no_perreo():
    blocks = build_system_blocks("gate_sardinia", "", "lunedi 1 gennaio 2026, 12:00")
    assert "UPSELL PERREO" not in blocks[0]["text"]
