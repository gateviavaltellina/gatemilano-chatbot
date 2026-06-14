import eval.consolidate_corrections as cc


def test_apply_edit_under_existing_heading():
    kb = "# Titolo\n\n## Biglietti\n- riga esistente\n\n## Altro\n- x\n"
    out = cc._apply_edit(kb, "## Biglietti", "nuova regola biglietti")
    lines = out.split("\n")
    i = lines.index("## Biglietti")
    assert lines[i + 1] == "- nuova regola biglietti"
    assert "- riga esistente" in out  # non rimuove l'esistente


def test_apply_edit_fallback_when_heading_missing():
    kb = "# Titolo\n\n## Biglietti\n- x\n"
    out = cc._apply_edit(kb, "## Inesistente", "regola orfana")
    assert cc._CONSOLIDATED_SECTION in out
    assert "- regola orfana" in out


def test_apply_edit_dedup_no_change_if_present():
    kb = "# Titolo\n\n## Biglietti\n- regola gia presente\n"
    out = cc._apply_edit(kb, "## Biglietti", "regola gia presente")
    assert out == kb
