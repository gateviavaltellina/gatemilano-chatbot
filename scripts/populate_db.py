#!/opt/homebrew/bin/python3.12
"""
Popola manualmente ChromaDB con la knowledge base statica e triggera una sync Xceed.
Uso: python3 scripts/populate_db.py
"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from rag.chromadb_manager import chromadb_manager
from sync.xceed_sync import sync_all_venues

async def main():
    print("Inizializzazione ChromaDB...")
    await chromadb_manager.init()
    print("Knowledge base statica caricata.")
    print("Avvio sync Xceed...")
    await sync_all_venues()
    print("Fatto.")

if __name__ == "__main__":
    asyncio.run(main())
