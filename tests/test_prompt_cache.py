from ai.claude_client import build_system_blocks


def test_two_blocks_static_first_with_cache_control():
    blocks = build_system_blocks("gate_milano", "EVENTI: x", "lunedi 1 gennaio 2026, 12:00")
    assert len(blocks) == 2
    assert blocks[0]["cache_control"] == {"type": "ephemeral", "ttl": "1h"}
    assert "REGOLE FONDAMENTALI" in blocks[0]["text"]
    assert "UPSELL PERREO" in blocks[0]["text"]  # sezione Perreo (Milano) sta nello statico
    assert "cache_control" not in blocks[1]


def test_knowledge_base_is_in_cached_static_block():
    # La knowledge base (costante per venue) deve stare nel blocco cacheato,
    # non in quello dinamico — è il fix che porta la quota cacheabile a ~97%.
    blocks = build_system_blocks("gate_milano", "EVENTI: x", "lunedi 1 gennaio 2026, 12:00")
    assert "INFORMAZIONI FISSE VENUE" in blocks[0]["text"]
    # il blocco statico deve essere sostanzioso (rules + KB), non solo le regole
    assert len(blocks[0]["text"]) > 10000


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


def test_language_rule_at_end_of_dynamic_block():
    # La regola-lingua prioritaria deve stare nel blocco DINAMICO e IN FONDO (dopo il
    # contesto RAG), così è l'ultima istruzione che il modello legge prima di rispondere.
    for venue in ("gate_milano", "gate_sardinia"):
        dyn = build_system_blocks(venue, "EVENTI: x", "lunedi 1 gennaio 2026, 12:00")[1]["text"]
        assert "LINGUA DELLA RISPOSTA" in dyn
        assert "TRADUCI" in dyn
        assert dyn.index("LINGUA DELLA RISPOSTA") > dyn.index("EVENTI: x")


def test_corrections_injected_in_dynamic_block(monkeypatch):
    seen = []
    monkeypatch.setattr(
        "rag.corrections.get_rules_text",
        lambda venue: seen.append(venue) or "CORREZIONI STAFF (priorità massima):\n- regola di test",
    )
    blocks = build_system_blocks("gate_milano", "contesto rag", "lunedì 14 giugno 2026, 22:00")
    static_text, dynamic_text = blocks[0]["text"], blocks[1]["text"]
    # le correzioni stanno nel blocco dinamico, NON in quello statico cacheato
    assert "regola di test" in dynamic_text
    assert "regola di test" not in static_text
    # il blocco statico resta cacheato
    assert blocks[0]["cache_control"]["type"] == "ephemeral"
    # il venue viene inoltrato a get_rules_text
    assert seen == ["gate_milano"]


def test_no_corrections_no_block(monkeypatch):
    monkeypatch.setattr("rag.corrections.get_rules_text", lambda venue: "")
    blocks = build_system_blocks("gate_milano", "contesto rag", "lunedì 14 giugno 2026, 22:00")
    # niente sezione correzioni e nessun prefisso accidentale "\n\n"
    assert "CORREZIONI STAFF" not in blocks[1]["text"]
    assert blocks[1]["text"].startswith("DATA E ORA ATTUALE:")
