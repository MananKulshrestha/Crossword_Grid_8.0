"""Query the LightRAG index built by ingest.py, in "mix" mode (combines
knowledge-graph traversal with vector retrieval).

Returns raw grounded context (chunks/entities/relationships + source SKU
references) rather than an LLM-synthesized prose answer -- per the
retrieval-architecture doc, response generation belongs to the outer
chat/response-writer layer, not to this retrieval step. This is what a
future search_catalog integration would consume and pass into the
candidate merger, not a final answer to show a user directly.

Usage:
    python query.py "your question here"
"""

import asyncio
import sys

from lightrag import QueryParam

from ingest import build_rag


async def main():
    if len(sys.argv) < 2:
        print("Usage: python query.py \"your question here\"", file=sys.stderr)
        sys.exit(1)
    question = " ".join(sys.argv[1:])

    rag = await build_rag()
    context = await rag.aquery(
        question, param=QueryParam(mode="mix", only_need_context=True)
    )
    print(context)
    await rag.finalize_storages()


if __name__ == "__main__":
    asyncio.run(main())
