from pydantic_settings import BaseSettings
from pathlib import Path

class Settings(BaseSettings):
    # Anthropic
    anthropic_api_key: str
    model: str = "claude-sonnet-4-6"

    # WhatsApp
    wa_phone_number_id: str = "1021019861105099"
    wa_access_token: str
    wa_verify_token: str = "gate_whatsapp_verify_2025"
    wa_business_account_id: str = "959741779797746"
    wa_api_url: str = "https://graph.facebook.com/v25.0"

    # Meta app secret per la verifica firma webhook (X-Hub-Signature-256).
    # Se vuoto, la verifica è disabilitata (retro-compatibile, ma sconsigliato in prod).
    meta_app_secret: str = ""

    # Instagram
    ig_api_url: str = "https://graph.instagram.com/v22.0"
    ig_gatemilano_id: str = "35517015101275600"
    ig_gatemilano_token: str = ""
    ig_gatesardinia_id: str = "24588954374135134"
    ig_gatesardinia_token: str = ""

    # Xceed
    xceed_api_key: str = ""

    # Sito Gate Milano: endpoint pubblico disponibilità tavoli VIP (single source of
    # truth, lo stesso usato dalla mappa di prenotazione). Per Milano si usa questo
    # invece della pipeline Xceed diretta.
    site_base_url: str = "https://gatemilano.it"

    # Sito Gate Sardinia: il checkout tavoli VIP è self-hosted (Revolut + Sanity),
    # distinto da Xceed (Milano). La disponibilità live è su
    # GET /api/vip/availability?event=<sanityId> e la pagina di prenotazione/pagamento
    # è /tavoli?event=<sanityId>.
    sardinia_site_base_url: str = "https://www.gatesardinia.it"

    # Notifications
    discord_webhook_url: str = ""
    discord_ig_webhook_url: str = ""
    discord_bot_token: str = ""
    discord_channel_id: int = 0
    # Canale Discord dedicato all'agent di gruppo WhatsApp.
    # Preferito: webhook URL (robusto). In alternativa: channel id (post via bot).
    discord_group_webhook_url: str = ""
    discord_group_channel_id: int = 0

    # Bot loop prevention — numeri WhatsApp (E.164, es. "393331234567") da ignorare
    wa_ignored_phones: str = ""

    # Agent staff nei gruppi WhatsApp: group_id consentiti (CSV). Vuoto = nessun
    # gruppo abilitato (l'agent di gruppo non risponde finché non li elenchi).
    wa_group_allowlist: str = ""

    # Token per l'endpoint di export degli eval case generati dalle correzioni.
    # Vuoto = endpoint disabilitato (404).
    eval_export_token: str = ""

    # Secret per il webhook Sanity (POST /webhook/sanity): permette il sync immediato
    # alla pubblicazione/modifica di un evento, senza aspettare il polling. Lo si passa
    # via header X-Webhook-Secret / Authorization: Bearer, o query ?key=. Vuoto =
    # endpoint NON protetto (sync attivabile da chiunque: sconsigliato in prod).
    sanity_webhook_secret: str = ""

    # App
    max_history: int = 8
    port: int = 8000

    # Persistenza stato conversazioni: directory su cui salvare lo stato (es. un
    # volume Railway montato a /data). Vuoto = persistenza disabilitata (in memoria).
    persist_dir: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}

settings = Settings()
KNOWLEDGE_DIR = Path(__file__).parent / "rag" / "knowledge"
