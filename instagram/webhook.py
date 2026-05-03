import logging
from config import settings
from rag.chromadb_manager import chromadb_manager
from ai.claude_client import generate_response
from notifications.discord import notify_conversation
from instagram.client import send_ig_message

logger = logging.getLogger(__name__)

_ig_conversations: dict[str, dict] = {}


def _venue_for_account(ig_account_id: str) -> str:
    if ig_account_id == settings.ig_gatesardinia_id:
        return "gate_sardinia"
    return "gate_milano"


def _get_conversation(ig_account_id: str, sender_id: str) -> dict:
    key = f"ig_{ig_account_id}_{sender_id}"
    if key not in _ig_conversations:
        _ig_conversations[key] = {"history": []}
    return _ig_conversations[key]


def _add_to_history(conv: dict, role: str, content: str) -> None:
    conv["history"].append({"role": role, "content": content})
    if len(conv["history"]) > settings.max_history * 2:
        conv["history"] = conv["history"][-settings.max_history * 2:]


async def process_ig_message(ig_account_id: str, sender_id: str, text: str) -> None:
    venue = _venue_for_account(ig_account_id)
    conv = _get_conversation(ig_account_id, sender_id)

    rag_context = await chromadb_manager.query(venue, text, top_k=settings.rag_top_k)

    _add_to_history(conv, "user", text)
    reply = await generate_response(
        venue=venue,
        user_message=text,
        rag_context=rag_context,
        history=conv["history"][:-1],
    )
    _add_to_history(conv, "assistant", reply)

    await send_ig_message(ig_account_id, sender_id, reply)
    await notify_conversation(f"ig:{sender_id[:12]}", venue, text, reply)
