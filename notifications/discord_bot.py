import asyncio
import logging
import discord
from config import settings

logger = logging.getLogger(__name__)

# phone → display name del membro staff che ha preso in carico
_human_sessions: dict[str, str] = {}
# discord message_id → phone
_msg_to_phone: dict[str, str] = {}
# discord message_id → extra context (ig_account_id, sender_id per IG)
_msg_context: dict[str, dict] = {}


def register_message(message_id: str, phone: str, context: dict = None) -> None:
    mid = str(message_id)
    _msg_to_phone[mid] = phone
    if context:
        _msg_context[mid] = context
    if len(_msg_to_phone) > 2000:
        for k in list(_msg_to_phone.keys())[:500]:
            _msg_to_phone.pop(k, None)
            _msg_context.pop(k, None)


def is_human_takeover(phone: str) -> bool:
    return phone in _human_sessions


intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


def _phone_from_reply(message: discord.Message):
    if not message.reference:
        return None, None
    mid = str(message.reference.message_id)
    return _msg_to_phone.get(mid), _msg_context.get(mid)


_EVENTI_VENUES = {"gate_milano": "milano", "gate_sardinia": "sardinia"}


def parse_eventi_command(text: str) -> list[str] | None:
    """Riconosce '!eventi [milano|sardinia]'. Ritorna le venue richieste, [] se
    l'argomento non è riconosciuto, None se non è il comando !eventi."""
    t = (text or "").strip()
    if t == "!eventi":
        return list(_EVENTI_VENUES)
    if t.startswith("!eventi "):
        arg = t[len("!eventi "):].strip().lower()
        if "sard" in arg or "budoni" in arg:
            return ["gate_sardinia"]
        if "mil" in arg:
            return ["gate_milano"]
        return []
    return None


def handle_eventi(venues: list[str]) -> str:
    """Diagnostica staff: cosa ha il bot IN MEMORIA adesso (store eventi).
    Serve a distinguere in 10 secondi 'il sync non ha l'evento' (problema dati/sync,
    es. evento assente o titolo placeholder su Sanity) da 'il bot ce l'ha ma risponde
    male' (problema di prompt/recupero) — senza aspettare lo screenshot di un cliente."""
    from rag.event_store import count, get_upcoming_events_compact
    if not venues:
        return "❌ Venue sconosciuta. Usa: !eventi, !eventi milano, !eventi sardinia"
    parts = []
    for v in venues:
        label = v.replace("_", " ").title()
        compact = get_upcoming_events_compact(v, days=14)
        header = f"📅 **{label}** — {count(v)} eventi in memoria"
        parts.append(f"{header}\n{compact}" if compact else f"{header}\n(nessun evento nei prossimi 14 giorni)")
    out = "\n\n".join(parts)
    # Limite messaggi Discord: 2000 caratteri
    return out if len(out) <= 1900 else out[:1900] + "\n…(troncato)"


async def handle_sync() -> str:
    """Forza un re-sync immediato da Sanity (+ Xceed). Serve a riflettere SUBITO le
    modifiche fatte nel CMS — un evento annullato/tolto, aggiunto o corretto — senza
    aspettare il cron da 2h. Caso reale: serate annullate in giornata mentre il bot
    continuava a proporle."""
    from rag.event_store import count
    try:
        from sync.sanity_sync import sync_all_venues as _sanity
        await _sanity()
    except Exception as e:
        return f"❌ Sync Sanity fallito: {e}"
    try:
        from sync.xceed_sync import sync_all_venues as _xceed
        await _xceed()
    except Exception as e:
        # Xceed è secondario: il sync Sanity (eventi Sardegna/Milano) è già andato.
        return (f"🔄 Sync Sanity ok (Xceed fallito: {e}). Eventi in memoria: "
                f"Milano {count('gate_milano')}, Sardegna {count('gate_sardinia')}.")
    return (f"🔄 Sync completato. Eventi in memoria: "
            f"Milano {count('gate_milano')}, Sardegna {count('gate_sardinia')}.")


async def handle_tavoli(days: int = 30) -> str:
    """!tavoli [giorni] — copertura tavoli VIP Milano: per ogni evento in calendario
    interroga l'endpoint live del sito e riassume lo stato (liberi/chiusi/venduti,
    prezzi, eventi senza mappa). Serve a scovare in anticipo serate con la vendita
    tavoli non configurata o nomi che non agganciano la mappa (fallimento silenzioso
    del lookup — caso reale Nikolina 16/10 quotata coi prezzi statici)."""
    import httpx as _httpx
    from config import settings as _s
    from rag.event_store import get_vip_candidates

    cands = get_vip_candidates("gate_milano", days=days)[:25]
    if not cands:
        return f"Nessun evento Milano nei prossimi {days} giorni."
    lines = [f"🪑 **Copertura tavoli Milano** (prossimi {days} giorni, live dal sito):"]
    base = _s.site_base_url.rstrip("/")
    async with _httpx.AsyncClient(timeout=10, headers={"User-Agent": "Mozilla/5.0"}) as client:
        for name, date_iso, _tu, _sid in cands:
            try:
                r = await client.get(f"{base}/api/vip-availability",
                                     params={"name": name, "date": date_iso})
                if r.status_code == 404:
                    status = "❌ senza mappa tavoli sul sito"
                elif r.status_code != 200:
                    status = f"⚠️ HTTP {r.status_code}"
                else:
                    tabs = r.json().get("tables", [])
                    if not tabs:
                        status = "— nessun tavolo configurato"
                    else:
                        free = sum(1 for t in tabs if t.get("stato") == "libero")
                        chiusi = sum(1 for t in tabs if t.get("stato") == "chiuso")
                        sold = len(tabs) - free - chiusi
                        prezzi = "/".join(f"€{p}" for p in sorted({t.get("prezzo") for t in tabs}))
                        parts = [f"✅ {free} liberi"] if free else []
                        if chiusi:
                            parts.append(f"🔒 {chiusi} vendita non aperta")
                        if sold:
                            parts.append(f"⛔ {sold} venduti")
                        status = " · ".join(parts) + f" · {prezzi}"
            except Exception:
                status = "⚠️ endpoint non raggiungibile"
            lines.append(f"`{date_iso[5:]}` {name[:34]}: {status}")
    out = "\n".join(lines)
    return out if len(out) <= 1900 else out[:1900] + "\n…(troncato)"


async def handle_stato() -> str:
    """!stato — diagnosi rapida per lo staff quando 'il bot non risponde':
    verifica DAL VIVO i token Meta (IG Milano/Sardegna + WhatsApp) riportando
    l'errore ESATTO di Meta se un canale è rotto (caso tipico: su Discord la
    risposta appare, su IG il cliente non riceve = token invalido → gli invii
    falliscono), più eventi in memoria e ultimo errore del modello."""
    from notifications.token_health import _targets, _token_ok
    from rag.event_store import count
    from ai.claude_client import last_api_error
    lines = ["🩺 **Stato bot**"]
    targets = _targets()
    if not targets:
        lines.append("⚠️ Nessun token configurato (env vuote?)")
    for name, url, token in targets:
        ok, detail = await _token_ok(url, token)
        if ok is True:
            lines.append(f"✅ {name}: token valido")
        elif ok is False:
            lines.append(
                f"🚨 {name}: **TOKEN NON VALIDO** — {detail}\n"
                "   → Gli invii da questo canale FALLISCONO. Rigenera il token su "
                "Meta Business e aggiorna la variabile su Railway."
            )
        else:
            lines.append(f"⚠️ {name}: check non concludente ({detail})")
    lines.append(
        f"📅 Eventi in memoria: Milano {count('gate_milano')}, "
        f"Sardegna {count('gate_sardinia')}"
    )
    # Errori di INVIO: un token può risultare valido mentre gli invii falliscono
    # (permessi messaging, finestra 24h, restrizioni account). Qui l'errore esatto.
    from instagram.client import last_send_error as ig_send_err
    from whatsapp.client import last_send_error as wa_send_err
    if ig_send_err():
        lines.append(f"📤 Ultimo errore di INVIO Instagram: {ig_send_err()}")
    if wa_send_err():
        lines.append(f"📤 Ultimo errore di INVIO WhatsApp: {wa_send_err()}")
    err = last_api_error()
    if err:
        lines.append(f"🧠 Ultimo errore API modello: {err}")
    # Webhook RESPINTI per firma: se >0 e in crescita, META_APP_SECRET su Railway
    # non corrisponde all'app secret dell'app Meta → il bot non riceve NIENTE.
    from webhook_security import signature_reject_stats
    rejected, last_rej = signature_reject_stats()
    if rejected:
        lines.append(
            f"🚫 **Webhook RESPINTI per firma non valida: {rejected}** (ultimo: {last_rej})\n"
            "   → Il secret su Railway non corrisponde a chi firma: META_APP_SECRET = "
            "App Secret dell'app Meta (WhatsApp); META_APP_SECRET_IG = 'Segreto "
            "dell'app Instagram' (prodotto Instagram Login) per i webhook IG."
        )
    # Ultimi messaggi IN INGRESSO (traccia webhook): distingue "Meta non ci manda
    # nulla" (il tuo DM di prova NON compare qui) da "arriva ma viene scartato"
    # (compare con lo stage che spiega perché, es. mittente account del gruppo).
    from notifications.debug_trace import recent
    events = recent()[:6]
    if events:
        lines.append("📥 Ultimi messaggi in ingresso (più recente in alto):")
        for ev in events:
            extra = " · ".join(f"{k}={v}" for k, v in ev.items()
                               if k not in ("at", "channel", "sender", "text", "stage"))
            txt = (ev.get("text") or "").replace("\n", " ")[:60]
            lines.append(
                f"  `{ev['at'][11:]}` {ev['channel'].upper()} …{ev['sender']} [{ev['stage']}]"
                + (f" {txt}" if txt else "") + (f" ({extra})" if extra else "")
            )
    else:
        lines.append("📥 Nessun messaggio in ingresso tracciato dall'ultimo riavvio.")
    out = "\n".join(lines)
    return out if len(out) <= 1900 else out[:1900] + "\n…(troncato)"


def parse_correction_command(text: str):
    """Riconosce i comandi correzione. Ritorna (cmd, payload) o (None, '').

    Comandi case-sensitive (come il resto del bot: !r/!t/!rel). Non collidono coi
    comandi takeover: !regola/!rimuovi iniziano per !re/!ri, !regole è esatto."""
    t = (text or "").strip()
    if t.startswith("!regola "):
        return "regola", t[len("!regola "):].strip()
    if t == "!regole":
        return "regole", ""
    if t.startswith("!rimuovi "):
        return "rimuovi", t[len("!rimuovi "):].strip()
    if t.startswith("!approva "):
        return "approva", t[len("!approva "):].strip()
    return None, ""


def handle_correction_command(cmd: str, payload: str, ctx: dict, author: str) -> str:
    """Esegue i comandi correzione SINCRONI e ritorna il testo per Discord.
    Il comando !regola (che genera la bozza LLM) è gestito da handle_regola (async)."""
    from rag import corrections
    if cmd == "regole":
        items = corrections.list_corrections()
        if not items:
            return "Nessuna correzione attiva."
        lines = ["Correzioni attive:"]
        for c in items:
            stato = c.get("case_status")
            suffix = f" [eval: {stato}]" if stato else ""
            lines.append(f"#{c['id']} [{c['venue']}] {c['rule']}{suffix}")
        return "\n".join(lines)
    if cmd == "rimuovi":
        if not payload:
            return "❌ Indica l'id: !rimuovi <id>"
        ok = corrections.remove_correction(payload)
        return f"🗑️ Rimossa #{payload}." if ok else f"❌ Nessuna correzione con id {payload}."
    if cmd == "approva":
        if not payload:
            return "❌ Indica l'id: !approva <id>"
        ok = corrections.approve_case(payload)
        return f"✅ Eval case approvato per #{payload}." if ok else f"❌ Nessuna bozza da approvare per id {payload}."
    return ""


async def handle_regola(payload: str, ctx: dict, author: str, *, client=None, model=None) -> str:
    """!regola: salva la correzione e genera la bozza di eval case (LLM)."""
    from rag import corrections, correction_cases
    if not ctx or not ctx.get("venue"):
        return "❌ Rispondi a un messaggio di conversazione del bot per usare !regola"
    if not payload:
        return "❌ Scrivi la regola dopo !regola (es. !regola per i rimborsi manda sempre a info@)"
    venue = ctx["venue"]
    example = {"user_msg": ctx.get("user_msg", ""), "wrong_reply": ctx.get("bot_reply", "")}
    cid = corrections.add_correction(venue, payload, example, author)
    count = len(corrections.list_corrections(venue))
    msg = f"✅ Regola salvata (#{cid}) per {venue}. Si applica da subito."
    if count > corrections.SOFT_CAP:
        msg += f"\n⚠️ {count} correzioni per {venue}: conviene consolidarle nella KB."
    if client is None:
        from ai.claude_client import _client as client
    model = model or settings.model
    correction = corrections.get_correction(cid)
    case = await correction_cases.draft_case(correction, client=client, model=model) if correction else None
    if case:
        corrections.set_case(cid, case)
        must = "; ".join(case["rubric"]["must"]) or "—"
        mustnot = "; ".join(case["rubric"]["must_not"]) or "—"
        msg += f"\n📋 Bozza eval: MUST: {must} | MUST NOT: {mustnot}\nApprova con !approva {cid}"
    else:
        msg += "\n⚠️ Bozza eval non generata, riprova più tardi."
    return msg


@bot.event
async def on_ready():
    logger.info("Discord bot connesso come %s", bot.user)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()
    phone, ctx = _phone_from_reply(message)

    # !eventi è diagnostica read-only: funziona in QUALUNQUE canale il bot legga
    # (lo staff usa canali diversi per venue), quindi PRIMA del filtro canale.
    eventi_venues = parse_eventi_command(content)
    if eventi_venues is not None:
        await message.reply(handle_eventi(eventi_venues), mention_author=False)
        return

    # !sync: forza il re-sync immediato da Sanity (azione admin read/write), in
    # QUALUNQUE canale come !eventi. Utile dopo aver annullato/modificato un evento
    # nel CMS, per non aspettare il cron da 2h.
    if content == "!sync":
        await message.add_reaction("🔄")
        await message.reply(await handle_sync(), mention_author=False)
        return

    # !stato: diagnosi read-only (token Meta dal vivo, eventi, errori modello),
    # in QUALUNQUE canale come !eventi. Per i casi "il bot non risponde più".
    if content == "!stato":
        await message.add_reaction("🩺")
        await message.reply(await handle_stato(), mention_author=False)
        return

    # !tavoli [giorni]: copertura tavoli VIP Milano dal sito, on-demand.
    if content == "!tavoli" or content.startswith("!tavoli "):
        arg = content[len("!tavoli"):].strip()
        days = int(arg) if arg.isdigit() else 30
        await message.add_reaction("🪑")
        await message.reply(await handle_tavoli(days), mention_author=False)
        return

    # Le notifiche WhatsApp e Instagram vivono su canali Discord DIVERSI. Una reply
    # a una notifica registrata del bot va gestita in QUALUNQUE canale (altrimenti
    # il takeover IG non funziona se discord_channel_id punta al canale WA). Il
    # filtro su discord_channel_id resta solo per il rumore non-reply.
    if settings.discord_channel_id and message.channel.id != settings.discord_channel_id and not phone:
        return

    cmd, payload = parse_correction_command(content)
    if cmd == "regola":
        reply = await handle_regola(payload, ctx, message.author.display_name)
        if reply:
            await message.reply(reply, mention_author=False)
        return
    if cmd:
        reply = handle_correction_command(cmd, payload, ctx, message.author.display_name)
        if reply:
            await message.reply(reply, mention_author=False)
        return

    if content.startswith("!r "):
        if not phone:
            await message.reply("❌ Rispondi a una notifica del bot per usare !r", mention_author=False)
            return
        text = content[3:].strip()
        if not text:
            return
        if phone.startswith("ig:") and ctx:
            from instagram.client import send_ig_message
            await send_ig_message(ctx["ig_account_id"], ctx["sender_id"], text)
        else:
            from whatsapp.client import send_message
            await send_message(phone, text)
        _human_sessions[phone] = message.author.display_name
        await message.add_reaction("✅")

    elif content == "!t":
        if not phone:
            await message.reply("❌ Rispondi a una notifica del bot per usare !t", mention_author=False)
            return
        _human_sessions[phone] = message.author.display_name
        await message.add_reaction("🤝")
        await message.channel.send(
            f"✋ **{message.author.display_name}** ha preso in carico `...{phone[-4:]}` — bot in pausa.",
            delete_after=60,
        )

    elif content in ("!rel", "!release"):
        if phone and phone in _human_sessions:
            del _human_sessions[phone]
            await message.add_reaction("🤖")
            await message.channel.send(
                f"🤖 Bot riattivato per `...{phone[-4:]}`",
                delete_after=60,
            )
        else:
            await message.reply("❌ Conversazione non in takeover", mention_author=False)


async def post_embed_to_channel(channel_id: int, description: str, fields: list, color: int) -> bool:
    """Posta un embed in un canale per ID usando il bot (per i canali dedicati,
    es. l'agent di gruppo WhatsApp). Ritorna False se il bot non è pronto / non
    vede il canale, così il chiamante può fare fallback al webhook."""
    if not channel_id or not bot.is_ready():
        return False
    channel = bot.get_channel(channel_id)
    if channel is None:
        return False
    try:
        embed = discord.Embed(description=description, color=color)
        for f in fields:
            embed.add_field(name=f.get("name") or "​", value=f.get("value") or "​", inline=f.get("inline", False))
        await channel.send(embed=embed)
        return True
    except Exception as e:
        logger.warning("post_embed_to_channel fallito (%s): %s", channel_id, e)
        return False


async def start() -> None:
    if not settings.discord_bot_token:
        logger.info("DISCORD_BOT_TOKEN non configurato — human takeover disabilitato")
        return
    try:
        await bot.start(settings.discord_bot_token)
    except Exception as e:
        logger.error("Discord bot errore: %s", e)
