import chromadb
import logging
from pathlib import Path
from chromadb.utils import embedding_functions
from config import settings, KNOWLEDGE_DIR

logger = logging.getLogger(__name__)
COLLECTIONS = ["gate_milano", "gate_sardinia"]

class ChromaDBManager:
    def __init__(self):
        self._client: chromadb.PersistentClient | None = None
        self._collections: dict = {}
        self._ef = None

    async def init(self):
        Path(settings.chroma_db_path).mkdir(parents=True, exist_ok=True)
        self._client = chromadb.PersistentClient(path=settings.chroma_db_path)
        self._ef = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name=settings.embedding_model
        )
        for name in COLLECTIONS:
            self._collections[name] = self._client.get_or_create_collection(
                name=name, embedding_function=self._ef
            )
            logger.info("Collezione ChromaDB '%s' pronta", name)
        await self._populate_static_knowledge()

    async def _populate_static_knowledge(self):
        for collection_name in COLLECTIONS:
            col = self._collections[collection_name]
            venue_key = collection_name.replace("gate_", "")
            knowledge_file = KNOWLEDGE_DIR / f"{collection_name}.md"
            if not knowledge_file.exists():
                logger.warning("File knowledge non trovato: %s", knowledge_file)
                continue
            content = knowledge_file.read_text(encoding="utf-8")
            chunks = _chunk_markdown(content, chunk_size=400, overlap=50)
            ids = [f"static_{collection_name}_{i}" for i in range(len(chunks))]
            existing = col.get(ids=ids)
            existing_ids = set(existing["ids"])
            new_chunks = [(id_, chunk) for id_, chunk in zip(ids, chunks) if id_ not in existing_ids]
            if new_chunks:
                new_ids, new_docs = zip(*new_chunks)
                col.add(ids=list(new_ids), documents=list(new_docs))
                logger.info("Aggiunti %d chunk statici a '%s'", len(new_chunks), collection_name)

    def upsert_event(self, venue: str, event_id: str, document: str, metadata: dict):
        col = self._collections.get(venue)
        if col is None:
            return
        col.upsert(ids=[f"event_{event_id}"], documents=[document], metadatas=[metadata])

    def delete_stale_events(self, venue: str, current_event_ids: list[str]):
        col = self._collections.get(venue)
        if col is None:
            return
        all_items = col.get(where={"type": "event"})
        existing_ids = set(all_items["ids"])
        current_prefixed = {f"event_{eid}" for eid in current_event_ids}
        stale = existing_ids - current_prefixed
        if stale:
            col.delete(ids=list(stale))
            logger.info("Rimossi %d eventi scaduti da '%s'", len(stale), venue)

    async def query(self, venue: str, query_text: str, top_k: int = 5) -> str:
        col = self._collections.get(venue)
        if col is None:
            return ""
        results = col.query(query_texts=[query_text], n_results=min(top_k, col.count()))
        docs = results.get("documents", [[]])[0]
        return "\n\n---\n\n".join(docs)

    def get_events_for_date(self, venue: str, date_str: str) -> str:
        """Fetch events on a specific date (YYYY-MM-DD) via numeric timestamp filter."""
        col = self._collections.get(venue)
        if col is None:
            return ""
        try:
            from datetime import datetime, timezone as tz
            day_start = int(datetime.strptime(date_str[:10], "%Y-%m-%d")
                            .replace(tzinfo=tz.utc).timestamp())
            day_end = day_start + 86400
            results = col.get(
                where={"$and": [
                    {"type": {"$eq": "event"}},
                    {"date_ts": {"$gte": day_start}},
                    {"date_ts": {"$lt": day_end}},
                ]}
            )
            docs = results.get("documents", [])
            return "\n\n---\n\n".join(docs) if docs else ""
        except Exception as e:
            logger.warning("get_events_for_date error: %s", e)
            return ""

def _next_day_str(date_str: str) -> str:
    from datetime import datetime, timedelta
    dt = datetime.strptime(date_str[:10], "%Y-%m-%d")
    return (dt + timedelta(days=1)).strftime("%Y-%m-%d")


def _chunk_markdown(text: str, chunk_size: int = 400, overlap: int = 50) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = (current + "\n\n" + para).strip()
        else:
            if current:
                chunks.append(current)
            if len(para) > chunk_size:
                words = para.split()
                sub = ""
                for w in words:
                    if len(sub) + len(w) + 1 <= chunk_size:
                        sub = (sub + " " + w).strip()
                    else:
                        if sub:
                            chunks.append(sub)
                        sub = w
                if sub:
                    current = sub
                else:
                    current = ""
            else:
                current = para
    if current:
        chunks.append(current)
    return chunks if chunks else [text[:chunk_size]]

chromadb_manager = ChromaDBManager()
