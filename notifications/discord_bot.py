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
    return None, ""


def handle_correction_command(cmd: str, payload: str, ctx: dict, author: str) -> str:
    """Esegue il comando correzione e ritorna il testo di conferma per Discord."""
    from rag import corrections
    if cmd == "regola":
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
        return msg
    if cmd == "regole":
        items = corrections.list_corrections()
        if not items:
            return "Nessuna correzione attiva."
        lines = ["Correzioni attive:"]
        for c in items:
            lines.append(f"#{c['id']} [{c['venue']}] {c['rule']}")
        return "\n".join(lines)
    if cmd == "rimuovi":
        if not payload:
            return "❌ Indica l'id: !rimuovi <id>"
        ok = corrections.remove_correction(payload)
        return f"🗑️ Rimossa #{payload}." if ok else f"❌ Nessuna correzione con id {payload}."
    return ""


@bot.event
async def on_ready():
    logger.info("Discord bot connesso come %s", bot.user)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return

    content = message.content.strip()
    phone, ctx = _phone_from_reply(message)

    # Le notifiche WhatsApp e Instagram vivono su canali Discord DIVERSI. Una reply
    # a una notifica registrata del bot va gestita in QUALUNQUE canale (altrimenti
    # il takeover IG non funziona se discord_channel_id punta al canale WA). Il
    # filtro su discord_channel_id resta solo per il rumore non-reply.
    if settings.discord_channel_id and message.channel.id != settings.discord_channel_id and not phone:
        return

    cmd, payload = parse_correction_command(content)
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
