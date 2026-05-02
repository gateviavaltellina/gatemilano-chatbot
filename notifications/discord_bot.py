import asyncio
import logging
import discord
from config import settings

logger = logging.getLogger(__name__)

# phone → display name del membro staff che ha preso in carico
_human_sessions: dict[str, str] = {}
# discord message_id → phone
_msg_to_phone: dict[str, str] = {}


def register_message(message_id: str, phone: str) -> None:
    _msg_to_phone[str(message_id)] = phone
    if len(_msg_to_phone) > 2000:
        for k in list(_msg_to_phone.keys())[:500]:
            del _msg_to_phone[k]


def is_human_takeover(phone: str) -> bool:
    return phone in _human_sessions


intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)


def _phone_from_reply(message: discord.Message) -> str | None:
    if not message.reference:
        return None
    return _msg_to_phone.get(str(message.reference.message_id))


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

    if content.startswith("!reply "):
        phone = _phone_from_reply(message)
        if not phone:
            await message.reply("❌ Rispondi a una notifica del bot per usare !reply", mention_author=False)
            return
        text = content[len("!reply "):].strip()
        if not text:
            return
        from whatsapp.client import send_message
        await send_message(phone, text)
        _human_sessions[phone] = message.author.display_name
        await message.add_reaction("✅")

    elif content == "!takeover":
        phone = _phone_from_reply(message)
        if not phone:
            await message.reply("❌ Rispondi a una notifica del bot per usare !takeover", mention_author=False)
            return
        _human_sessions[phone] = message.author.display_name
        await message.add_reaction("🤝")
        await message.channel.send(
            f"✋ **{message.author.display_name}** ha preso in carico `...{phone[-4:]}` — bot in pausa.",
            delete_after=60,
        )

    elif content == "!release":
        phone = _phone_from_reply(message)
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
