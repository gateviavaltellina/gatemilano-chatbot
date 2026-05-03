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


@bot.event
async def on_ready():
    logger.info("Discord bot connesso come %s", bot.user)


@bot.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if settings.discord_channel_id and message.channel.id != settings.discord_channel_id:
        return

    content = message.content.strip()

    if content.startswith("!r "):
        phone, ctx = _phone_from_reply(message)
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
        phone, _ = _phone_from_reply(message)
        if not phone:
            await message.reply("❌ Rispondi a una notifica del bot per usare !t", mention_author=False)
            return
        _human_sessions[phone] = message.author.display_name
        await message.add_reaction("🤝")
        await message.channel.send(
            f"✋ **{message.author.display_name}** ha preso in carico `...{phone[-4:]}` — bot in pausa.",
            delete_after=60,
        )

    elif content == "!rel":
        phone, _ = _phone_from_reply(message)
        if phone and phone in _human_sessions:
            del _human_sessions[phone]
            await message.add_reaction("🤖")
            await message.channel.send(
                f"🤖 Bot riattivato per `...{phone[-4:]}`",
                delete_after=60,
            )
        else:
            await message.reply("❌ Conversazione non in takeover", mention_author=False)


async def start() -> None:
    if not settings.discord_bot_token:
        logger.info("DISCORD_BOT_TOKEN non configurato — human takeover disabilitato")
        return
    try:
        await bot.start(settings.discord_bot_token)
    except Exception as e:
        logger.error("Discord bot errore: %s", e)
