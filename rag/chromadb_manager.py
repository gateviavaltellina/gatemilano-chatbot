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
            knowledge_file = KNOWLEDGE_DIR / f"{collection_name}.md"
            if not knowledge_file.exists():
                logger.warning("File knowledge non trovato: %s", knowledge_file)
                continue
            content = knowledge_file.read_text(encoding="utf-8")
            chunks = _chunk_markdown(content, chunk_size=600, overlap=80)
            ids = [f"static_{collection_name}_{i}" for i in range(len(chunks))]
            # Upsert: aggiorna contenuto se il file è cambiato
            col.upsert(ids=ids, documents=chunks)
            logger.info("Upserted %d chunk statici in '%s'", len(chunks), collection_name)
            # Rimuovi chunk in eccesso (se il file è diventato più corto)
            probe_ids = [f"static_{collection_name}_{i}" for i in range(len(chunks), len(chunks) + 100)]
            stale = col.get(ids=probe_ids)
            if stale["ids"]:
                col.delete(ids=stale["ids"])
                logger.info("Rimossi %d chunk statici obsoleti da '%s'", len(stale["ids"]), collection_name)

    def upsert_event(self, venue: str, event_id: str, document: str, metadata: dict):
        col = self._collections.get(venue)
        if col is None:
            return
        col.upsert(ids=[f"event_{event_id}"], documents=[document], metadatas=[metadata])

    def delete_stale_events(self, venue: str, current_event_ids: list[str], source=None):
        col = self._collections.get(venue)
        if col is None:
            return
        if source:
            where = {"$and": [{"type": {"$eq": "event"}}, {"source": {"$eq": source}}]}
        else:
            where = {"type": "event"}
        all_items = col.get(where=where)
        existing_ids = set(all_items["ids"])
        current_prefixed = {f"event_{eid}" for eid in current_event_ids}
        stale = existing_ids - current_prefixed
        if stale:
            col.delete(ids=list(stale))
            logger.info("Rimossi %d eventi scaduti da '%s'%s", len(stale), venue, f" ({source})" if source else "")

    async def query(self, venue: str, query_text: str, top_k: int = 5) -> str:
        col = self._collections.get(venue)
        if col is None:
            return ""
        results = col.query(query_texts=[query_text], n_results=min(top_k, col.count()))
        docs = results.get("documents", [[]])[0]
        return "\n\n---\n\n".join(docs)

    def get_upcoming_events(self, venue: str, days: int = 14) -> str:
        """Fetch all events in the next N days, sorted by date."""
        col = self._collections.get(venue)
        if col is None:
            return ""
        try:
            from datetime import datetime, timezone as tz
            now_ts = int(datetime.now(tz.utc).timestamp())
            end_ts = now_ts + days * 86400
            results = col.get(
                where={"$and": [
                    {"type": {"$eq": "event"}},
                    {"date_ts": {"$gte": now_ts}},
                    {"date_ts": {"$lte": end_ts}},
                ]},
                include=["documents", "metadatas"],
            )
            docs = results.get("documents", [])
            metas = results.get("metadatas", [])
            if not docs:
                return ""
            paired = sorted(zip(metas, docs), key=lambda x: x[0].get("date_ts", 0) if x[0] else 0)
            return "\n\n---\n\n".join(d for _, d in paired)
        except Exception as e:
            logger.warning("get_upcoming_events error: %s", e)
            return ""

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
