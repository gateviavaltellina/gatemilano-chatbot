"""Compatibilità con le famiglie di modelli recenti (Sonnet 5 / Opus 5 / Fable 5).

Incidente reale (30/8): passando MODEL=claude-sonnet-5 i clienti su Instagram
hanno iniziato a ricevere "al momento non riesco a rispondere", con in log
`AttributeError: 'ThinkingBlock' object has no attribute 'text'`. Causa: su quei
modelli il thinking è ATTIVO di default, quindi `response.content[0]` è un blocco
di ragionamento e non il testo. Sugli stessi modelli `temperature` è stato
rimosso e restituisce 400.
"""
import pytest

from ai import claude_client as cc


class _Block:
    def __init__(self, type_, text=None, thinking=None):
        self.type = type_
        if text is not None:
            self.text = text
        if thinking is not None:
            self.thinking = thinking


class _Resp:
    def __init__(self, blocks, stop_reason="end_turn"):
        self.content = blocks
        self.stop_reason = stop_reason


def test_first_text_salta_il_blocco_di_thinking():
    # Esattamente la forma che ha rotto la produzione: thinking in testa, testo dopo.
    resp = _Resp([
        _Block("thinking", thinking="sto ragionando..."),
        _Block("text", text="Ciao! Venerdì siamo aperti dalle 23."),
    ])
    assert cc.first_text(resp) == "Ciao! Venerdì siamo aperti dalle 23."


def test_first_text_senza_thinking_invariato():
    resp = _Resp([_Block("text", text="Risposta semplice")])
    assert cc.first_text(resp) == "Risposta semplice"


def test_first_text_vuoto_se_manca_il_testo():
    # Risposta troncata: solo thinking, nessun testo → il chiamante deve accorgersene
    # invece di mandare una risposta vuota al cliente.
    resp = _Resp([_Block("thinking", thinking="...")], stop_reason="max_tokens")
    assert cc.first_text(resp) == ""


@pytest.mark.parametrize("model", [
    "claude-sonnet-5", "claude-opus-5", "claude-fable-5",
    "claude-opus-4-8", "claude-opus-4-7",
])
def test_modelli_recenti_rifiutano_il_sampling(model):
    assert cc.supports_sampling(model) is False


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-haiku-4-5"])
def test_modelli_precedenti_accettano_il_sampling(model):
    assert cc.supports_sampling(model) is True


@pytest.mark.parametrize("model", ["claude-sonnet-5", "claude-opus-5", "claude-fable-5"])
def test_thinking_spento_sui_modelli_che_pensano_di_default(model):
    # Con max_tokens stretto (DM brevi) il thinking mangerebbe il budget: va spento
    # esplicitamente, altrimenti la risposta arriva vuota o troncata.
    assert cc.thinking_param(model) == {"type": "disabled"}


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-haiku-4-5"])
def test_nessun_parametro_thinking_sui_modelli_precedenti(model):
    # Sui modelli che NON pensano di default non va passato nulla: il parametro
    # potrebbe non essere accettato e il comportamento è già quello voluto.
    assert cc.thinking_param(model) is None
