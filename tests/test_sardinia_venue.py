"""Regressioni per il go-live Gate Sardinia: prompt per-venue + drinklist venue-aware.

Garantiscono che (a) il prompt Sardegna non contenga più dati Milano hardcoded,
(b) il prompt Milano resti col suo contenuto, (c) la drinklist sia selezionata per venue.
"""
import os

from ai.claude_client import build_system_blocks
from whatsapp.webhook import _DRINKLISTS


def _static(venue: str) -> str:
    return build_system_blocks(venue, "RAG", "DT")[0]["text"]


# --- Prompt Sardegna: niente contatti/fatti Milano ---

def test_sardinia_prompt_has_no_milano_contacts():
    s = _static("gate_sardinia")
    # NB: "329 169 6882" NON è più in lista — lo staff lo usa anche per il free entry
    # dei lavoratori di Budoni (contatto condiviso), vedi sezione dedicata nella KB.
    # ECCEZIONE VOLUTA: il ritiro delle vincite dei giveaway è gestito dal marketing
    # di GRUPPO (marketing@gatemilano.com), centralizzato per entrambe le sedi. È
    # l'unico contatto Milano ammesso in Sardegna: lo togliamo prima del check, così
    # ogni ALTRO riferimento gatemilano/marketing continua a essere vietato.
    s = s.replace("marketing@gatemilano.com", "")
    for milano_token in (
        "gatemilano", "Antonio", "389 640 6077",
        "marketing@", "Main Room", "Club Room", "Carroponte", "Valtellina",
    ):
        assert milano_token not in s, f"Sardegna non deve contenere '{milano_token}'"


def test_sardinia_prompt_has_no_milano_hours():
    s = _static("gate_sardinia")
    # NB: il singolo "23:00" ora è legittimo (cutoff del biglietto ridotto €5, valido
    # entrando entro le 23:00) e anche "05:00" lo è (closing party 22/8 fino alle 5)
    # — la guardia blocca la FINESTRA ORARIA di Milano.
    assert "23:00 – 05:00" not in s and "23:00 - 05:00" not in s
    # orario fisso Gate Sardinia: 22:00 – 04:00 (definitivo da agosto 2026)
    assert "22:00" in s and "04:00" in s
    assert "22:00 – 03:00" not in s  # il vecchio orario non deve più comparire
    # lo schema orari per giorno pre-go-live non deve comparire
    assert "18:30 – 02:30" not in s


def test_sardinia_prompt_has_ticket_access_guardrail():
    # caso reale: il bot diceva "col Posto Unico puoi stare dove vuoi, palco incluso"
    # (falso: le zone a bordo palco sono VIP riservate). La KB deve dare la regola.
    s = _static("gate_sardinia")
    assert "Posto Unico" in s
    assert "palco incluso" in s  # citato come errore da NON fare
    assert "area generale in piedi" in s


def test_sardinia_age_policy_16plus_under16_parent():
    # policy staff: eventi 16+ (dai 16 col documento, senza accompagnatore); sotto i
    # 16 serve un genitore presente per tutta la serata.
    s = _static("gate_sardinia")
    assert "16" in s
    assert "genitore" in s.lower()
    # niente più "18 di norma" né "qualsiasi età con un maggiorenne"
    assert "a QUALSIASI età SE accompagnato" not in s
    assert "dai 16 anni un minorenne può entrare SE accompagnato" not in s


def test_sardinia_prompt_has_budoni_workers_free_entry():
    # info fornita dallo staff: lavoratori di Budoni free entry solo su alcune serate,
    # contatto WhatsApp +39 329 169 6882.
    s = _static("gate_sardinia")
    assert "Budoni" in s and "329 169 6882" in s
    assert "alcune serate" in s


def test_sardinia_prompt_has_stage_policy():
    # caso reale: "c'è un palco o cantano vicino al dj?" — il bot deflettava all'email.
    # Deve sapere: artisti sul palco; eccezione (altra sala) solo su decisione artisti.
    s = _static("gate_sardinia")
    assert "palco" in s.lower()
    assert "altra sala" in s.lower()
    assert "decisione degli artisti" in s.lower()


def test_sardinia_prompt_has_navette_contact():
    # nuovo contatto driver/transfer (agosto 2026): il numero nuovo deve esserci e il
    # vecchio (Navette Orosei/Salvatore) può comparire SOLO nella nota che lo dismette.
    s = _static("gate_sardinia")
    assert "349 219 7091" in s
    assert "NON va più dato" in s


def test_sardinia_prompt_uses_ticketsms_not_xceed_dice_as_platform():
    s = _static("gate_sardinia")
    assert "ticketsms.it" in s
    # Xceed/Dice possono comparire solo nella regola che li VIETA, mai come piattaforma.
    assert "xceed.me" not in s
    assert "dice.fm" not in s


def test_sardinia_prompt_has_vip_zones_and_booking_channel():
    s = _static("gate_sardinia")
    assert "Terrace" in s
    assert "vip@gatesardinia.it" in s
    assert "+39 391 487 6443" in s


def test_sardinia_prompt_directs_tables_to_online_booking():
    s = _static("gate_sardinia")
    # i tavoli ora si prenotano/pagano online: il prompt deve citare il link del sito
    assert "gatesardinia.it/tavoli" in s
    # e NON deve più dire che la prenotazione online non è attiva
    assert "non è ancora attiva" not in s


# --- Drinklist: decisione di invio del PDF ---

def test_drinklist_explicit_request_always_sends():
    from whatsapp.webhook import _should_send_drinklist
    # richiesta esplicita → invia anche se già inviato in precedenza
    assert _should_send_drinklist("gate_sardinia", "mi giri il listino bottiglie?", "", already_sent=True)
    assert _should_send_drinklist("gate_sardinia", "mi mandi la drinklist?", "", already_sent=True)


def test_drinklist_implicit_trigger_sends_once():
    from whatsapp.webhook import _should_send_drinklist
    # parlando di tavoli: invio proattivo solo la prima volta
    assert _should_send_drinklist("gate_sardinia", "vorrei un tavolo", "", already_sent=False)
    assert not _should_send_drinklist("gate_sardinia", "vorrei un tavolo", "", already_sent=True)


def test_drinklist_unrelated_message_no_send():
    from whatsapp.webhook import _should_send_drinklist
    assert not _should_send_drinklist("gate_sardinia", "a che ora aprite?", "alle 22", already_sent=False)


def test_drinklist_unknown_venue_no_send():
    from whatsapp.webhook import _should_send_drinklist
    assert not _should_send_drinklist("gate_unknown", "mandami il listino", "", already_sent=False)


# --- Prompt Milano: contenuto preservato ---

def test_milano_prompt_keeps_its_content():
    s = _static("gate_milano")
    for milano_token in ("Main Room", "Antonio", "23:00 – 05:00", "info@gatemilano.com"):
        assert milano_token in s, f"Milano deve ancora contenere '{milano_token}'"


# --- Drinklist venue-aware ---

def test_drinklist_mapping_is_venue_aware():
    milano_url, milano_name = _DRINKLISTS["gate_milano"]
    sard_url, sard_name = _DRINKLISTS["gate_sardinia"]
    assert "drinklist_perreo.pdf" in milano_url
    assert "drinklist_sardegna.pdf" in sard_url
    assert milano_url != sard_url
    assert milano_name != sard_name


def test_sardinia_drinklist_pdf_exists_in_static():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    assert os.path.exists(os.path.join(root, "static", "drinklist_sardegna.pdf"))


def test_sardinia_has_jobs_info():
    # Assunzioni: età LAVORO 18+ e mail candidature, distinte dall'età d'ingresso (16+).
    s = _static("gate_sardinia")
    assert "jobs@gatesardinia.it" in s
    assert "18" in s and "LAVORARE" in s


def test_sardinia_ticket_availability_and_tone_rule():
    # caso reale: "quanti posti restano?" → il bot diceva "onestamente non so".
    from ai.claude_client import build_system_blocks
    blocks = build_system_blocks("gate_sardinia", "RAG", "DT")
    # KB: come funziona la disponibilità su TicketSMS (disponibile/esaurito, non il conteggio)
    assert "Disponibilità posti su TicketSMS" in blocks[0]["text"]
    # tono: "onestamente non so" citato come VIETATO nel blocco dinamico
    assert "onestamente non so" in blocks[1]["text"].lower()
    # e il .format del template dinamico non lascia placeholder rotti
    assert "{contact_email}" not in blocks[1]["text"]


def test_short_ambiguous_message_rule_in_dynamic_block():
    # caso reale: a un semplice "?" il bot ha risposto «non ho capito "agruatos"»,
    # parola mai scritta dal cliente. La regola vale per entrambe le sedi.
    from ai.claude_client import build_system_blocks
    for venue in ("gate_sardinia", "gate_milano"):
        dyn = build_system_blocks(venue, "RAG", "DT")[1]["text"]
        assert "MESSAGGI CORTI O AMBIGUI" in dyn
        assert "agruatos" in dyn  # citato come errore da NON ripetere


def test_sardinia_patente_now_accepted():
    # decisione staff 23/7 ("famo che anche patente va bene"): la patente è ACCETTATA
    # come documento d'ingresso; la tessera sanitaria resta non valida.
    s = _static("gate_sardinia")
    assert "Patente di guida: ACCETTATA" in s
    assert "non accettata in nessun caso" not in s
    assert "Tessera sanitaria" in s


def test_sardinia_5euro_ticket_valid_until_23():
    # regola staff 25/7: il biglietto da €5 vale solo entrando entro le 23:00;
    # dopo, va comprato l'intero. E mai dire "prezzo unico indipendente dall'orario".
    s = _static("gate_sardinia")
    assert "ENTRO le 23:00" in s
    assert "dopo le 23:00" in s.lower()


def test_sardinia_16plus_beats_ticketing_page_labels():
    # conferma staff: tutti gli eventi 16+. Se una pagina di biglietteria mostra
    # "18+" e l'evento nel contesto non lo indica, fa fede il 16+ del locale.
    s = _static("gate_sardinia")
    assert "TUTTI gli eventi" in s
    assert "etichetta imprecisa" in s


def test_sardinia_boarding_pass_promo_closed():
    # decisione staff 20/8: i Boarding Pass NON esistono più — il bot non deve
    # mai più proporli né linkarli (caso reale: proposti dopo la chiusura).
    s = _static("gate_sardinia")
    assert "PROMO CHIUSA" in s
    assert "NON proporre MAI più il Boarding Pass" in s
    assert "PROMO ATTIVA.** Il **Boarding Pass" not in s
    # e a chi chiede del Boarding Pass per il 15/22 va SEMPRE proposto il Free
    # Pass come alternativa (biglietto TicketSMS di un altro evento → gratis)
    assert "Alternativa da proporre SEMPRE" in s


def test_sardinia_5euro_supplement_rule():
    # regola generale definitiva: entro le 23 nessun extra; dopo, supplemento in
    # cassa +5 donna / +10 uomo oltre ai 5 già pagati. Mai "differenza fino al
    # prezzo pieno" (risposta reale sbagliata corretta dallo staff con !r).
    s = _static("gate_sardinia")
    assert "NON si paga NIENT'ALTRO" in s
    assert "+€5 la donna" in s and "+€10 l'uomo" in s
    assert "chiamare MAI quel prezzo" in s  # €4,30 online = stessa prevendita
    assert "Early Entry Ticket" in s        # nome reale su TicketSMS


def test_sardinia_provisional_document_accepted():
    # conferma staff (!r reale): il documento provvisorio cartaceo del Comune vale
    # come documento d'ingresso — il bot lo conferma senza rimandare all'email.
    s = _static("gate_sardinia")
    assert "PROVVISORIO del Comune: ACCETTATO" in s


def test_lo_zio_contact_in_both_venues():
    # richiesta staff 22/8: "lo zio"/"il boss" = Andrea Scirocco, contatto
    # condivisibile (caso reale: il bot rispondeva "non so chi sia lo zio" e
    # "i numeri non li fornisco mai").
    for venue in ("gate_milano", "gate_sardinia"):
        s = _static(venue)
        assert "LO ZIO" in s, venue
        assert "brillante e di grande carisma" in s, venue
        assert "90esimo + recupero" in s, venue
        assert "340 564 0389" in s, venue
        # niente cariche: "lo zio e basta" (indicazione staff)
        assert "amministratore di Gate" not in s, venue


def test_sardinia_canceled_events_permanent_memory():
    # caso reale 22/8: cliente con biglietto di Artie (annullato il 20/8, ormai
    # fuori dalla finestra eventi) → il bot diceva "non ho dettagli su quella
    # serata" e inventava una procedura di rimborso. La KB tiene la memoria
    # permanente degli annullati e la regola del rimborso automatico TicketSMS.
    s = _static("gate_sardinia")
    assert "Eventi ANNULLATI della stagione 2026" in s
    assert "Artie 5ive** — 20 agosto 2026 — ANNULLATO" in s
    assert "Akeem** — 12 agosto 2026 — ANNULLATO" in s
    assert "IN AUTOMATICO da TicketSMS" in s
    assert "entro il lunedì successivo" in s  # procedura inventata, citata come errore


def test_sardinia_season_closed_in_sordina():
    # chiusura anticipata (23/8): la serata del 29 non si farà, stagione conclusa.
    # Tono richiesto dallo staff: senza drammi, MAI promettere l'anno prossimo,
    # ma sempre "un'onda di mistero e speranza" (stile big company).
    s = _static("gate_sardinia")
    assert "STAGIONE 2026 CONCLUSA" in s
    assert "29 agosto (Perreo XL Closing Party) NON si farà" in s
    assert "mistero e speranza" in s
    assert "NON dire MAI esplicitamente" in s      # niente promesse sul futuro
    assert "Perreo XL Closing Party** — 29 agosto 2026 — ANNULLATO" in s
    assert "STAGIONE APERTA" not in s


def test_sardinia_ferragosto_free_pass_promo():
    # promo staff 11/8, estesa il 17/8 anche al 22/8: chi ha un biglietto TicketSMS
    # di un qualsiasi evento Gate Sardinia entra gratis ai Perreo XL del 15 e del 22
    # agosto mostrandolo all'ingresso. SOLO biglietti TicketSMS (no Fourvenues/liste).
    s = _static("gate_sardinia")
    assert "Free Pass" in s
    assert "sabato 15 agosto e sabato 22 agosto 2026" in s
    assert "mostralo all'ingresso" in s
    assert "Perreo XL" in s
    assert "SOLO biglietti TICKETSMS" in s
    # caso reale 22/8: "col biglietto di Artie entro stasera?" → il bot rispondeva
    # "non trasferibile" e mandava a comprare. La KB cita l'errore e la risposta giusta.
    assert "non è trasferibile" in s          # citato come errore da NON ripetere
    assert "entri GRATIS" in s
    assert "anche se la sua serata è passata o è stata annullata" in s


def test_sardinia_vip_tables_are_16plus():
    # correzione staff 10/8: il bot diceva "tavolo VIP 18 anni minimi, un 16enne
    # non può accedere all'area VIP" — falso. Tavoli/zone VIP = stessa età
    # dell'ingresso, 16+ con documento.
    s = _static("gate_sardinia")
    assert "Età minima al tavolo: **16 anni**" in s
    assert "richiede 18 anni minimi" in s  # citato come errore da NON ripetere
    assert "Età minima al tavolo: **18 anni**" not in s


def test_sardinia_same_name_tickets_are_fine():
    # caso reale: più biglietti intestati alla stessa persona → il bot inventava un
    # controllo nome-documento della security e rimandava a info@. Sono validi.
    s = _static("gate_sardinia")
    assert "più biglietti intestati alla stessa persona" in s
    assert "corrisponda al documento" in s  # citato come errore da NON ripetere


# --- Audit Milano (23/8) ---

def test_milano_audit_no_placeholders_or_stale_links():
    # esiti audit: niente placeholder LINK_DA_INSERIRE nel prompt (rischio che il
    # bot lo citi), niente link Dropbox (drinklist/mappe partono dal sistema).
    s = _static("gate_milano")
    assert "LINK_DA_INSERIRE" not in s
    assert "dropbox.com" not in s


def test_milano_audit_new_sections():
    s = _static("gate_milano")
    # Foto/video delle serate: sezione prima assente (esisteva solo in Sardegna)
    assert "Foto e Video delle Serate" in s
    assert "marketing@gatemilano.com" in s
    # Guestlist: citata nelle FAQ ma senza condizioni → guardia anti-allucinazione
    assert "Guestlist — anti-allucinazione" in s
    # tabella contatti arricchita
    assert "support@xceed.me" in s


def test_milano_backstage_offsite_pricing_guard():
    # caso reale (mail Carl Cox): il backstage di Via Valtellina (tabella €25-40,
    # tavoli €600) NON vale per gli eventi off-site — lì fanno fede i prezzi
    # della pagina evento (Carl Cox: Backstage Ticket €200).
    s = _static("gate_milano")
    assert "VALE SOLO PER GLI EVENTI IN VIA VALTELLINA" in s
    assert "Backstage Ticket €200" in s
    assert "indirizza a info@gatemilano.com" in s


def test_milano_carl_cox_tables_page():
    # richiesta staff: per i tavoli di Carl Cox reindirizzare SUBITO alla pagina
    # dedicata del sito (mappa 3D Carroponte + WhatsApp), non a info@.
    s = _static("gate_milano")
    assert "gatemilano.it/carlcox/tavoli" in s
    assert "mappa 3D del Carroponte" in s
    assert "+39 391 487 6443" in s


def test_milano_carl_cox_dedicated_drinklist():
    # drinklist evento-specifica Carl Cox (prezzi propri, ~doppi dello standard):
    # PDF in static/ + prezzi in KB, con divieto di citare i prezzi standard.
    import os as _os
    root = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    assert _os.path.exists(_os.path.join(root, "static", "drinklist_carlcox.pdf"))
    s = _static("gate_milano")
    assert "static/drinklist_carlcox.pdf" in s
    assert "Grey Goose .7l €600" in s          # prezzo Carl Cox
    assert "NON quelli della drinklist standard" in s
