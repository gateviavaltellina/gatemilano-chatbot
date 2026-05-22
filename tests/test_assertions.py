from eval.schema import Assertions
from eval.assertions import detect_markdown, run_assertions


def test_detect_markdown_finds_bold():
    assert detect_markdown("ecco il **prezzo** giusto") != []


def test_detect_markdown_finds_bullet_lines():
    assert detect_markdown("Opzioni:\n- uno\n- due") != []


def test_detect_markdown_clean_text_ok():
    assert detect_markdown("Stasera c'e Perreo XL alle 23, biglietti da 10 euro.") == []


def test_forbidden_substring_caught_case_insensitive():
    a = Assertions(forbidden_substrings=["database", "+39 391 487 6443"])
    fails = run_assertions("non e nel mio DATABASE", a)
    assert any("database" in f.lower() for f in fails)


def test_forbidden_markdown_flag():
    a = Assertions(forbidden_markdown=True)
    assert run_assertions("ecco **questo**", a) != []


def test_no_assertions_no_failures():
    assert run_assertions("qualsiasi testo", Assertions()) == []
