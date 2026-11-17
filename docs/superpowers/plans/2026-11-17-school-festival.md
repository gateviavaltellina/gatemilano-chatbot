# School Festival Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan.

**Goal:** Rispondere ai DM da @milano.school.festival per il solo evento del 22/12/2026.

**Architecture:** Profilo separato sul servizio esistente, token proprio, prompt
e scheda evento dedicati. Nessuna nuova pipeline calendario o biglietteria.

**Tech Stack:** Python, FastAPI, httpx, Anthropic, pytest.

**Spec:** ../specs/2026-11-17-school-festival-design.md

## Global Constraints

- Account e token festival opzionali; mai invio tramite credenziali Gate.
- Nessuna modifica ai contenuti Gate Milano/Sardinia.
- Nessuna promessa di disponibilità live o informazioni non verificate.

## Review Focus

- Entrambi i callback devono rispondere con ID/token festival.
- Token rinnovato deve sopravvivere al riavvio.
- Mittente identico su profili diversi non deve condividere takeover/storico.
- Domande su tavoli, date e altri eventi non devono importare dati Gate.
- Account sconosciuti e messaggi di altri bot non devono produrre risposte.

## Task 1: Account, contenuti e pipeline

Files: config.py, instagram/accounts.py, instagram/client.py,
instagram/webhook.py, instagram/token_store.py, whatsapp/webhook.py,
ai/school_festival.py, ai/claude_client.py, rag/school_festival.py,
rag/knowledge/milano_school_festival.md, rag/context_builder.py,
notifications/discord.py, notifications/token_health.py,
tests/test_school_festival.py, .env.example, CONFIGURATION.md.

- [x] Scrivere test: webhook dedicato/condiviso → richiesta HTTP inviata da
  ID festival con token festival; account sconosciuto/anti-loop → zero invii;
  contesto e prompt senza dati Gate; rollover data; persistenza token.
- [x] Eseguire test e verificare fallimenti per feature assente.
- [x] Implementare riconoscimento account e configurazione, prompt/context
  dedicati, token store e health check, etichette staff e isolamento takeover.
- [x] Eseguire test mirati e suite completa con credenziali di test (775 passati).
- [x] Rivedere diff e documentare configurazione/attivazione.

Revisione indipendente: corretti con test RED→GREEN il suggerimento di età
ereditato nelle story image e il conflitto ID opzionale che bloccava gli account
Gate. Primo giro suite: falso positivo del controllo sorgenti perché scandiva
le librerie nella `.venv`; spostato l'ambiente fuori dal repository, nessuna
modifica al test esistente. Prove con modello reale: prezzi/VIP, età e tavoli;
corretto il prompt per non dedurre assenza tavoli da informazioni mancanti.
Bio Instagram e scheda DICE verificati: link diretto, telefono pubblico, 16+.

## Task 2: Attivazione

- [ ] Verificare accesso Meta e collegare l'account corretto se autorizzabile.
- [ ] Configurare ID/token senza esporre segreti; deploy e health check.
- [ ] Verificare consegna reale se disponibile un DM di prova autorizzato;
  altrimenti riportare precisamente il passaggio che richiede l'utente.
