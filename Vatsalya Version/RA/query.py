"""Query the LightRAG index built by ingest.py, in "mix" mode (combines
knowledge-graph traversal with vector retrieval).

Usage:
    python query.py "your question here"
"""

import asyncio
import sys

from lightrag import QueryParam

from config import WORKING_DIR
from ingest import build_rag


async def main():
    question = " ".join(sys.argv[1:]) or "What kinds of footwear are in this catalog?"

    rag = await build_rag()
    answer = await rag.aquery(question, param=QueryParam(mode="mix"))
    print(answer)
    await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(main())
