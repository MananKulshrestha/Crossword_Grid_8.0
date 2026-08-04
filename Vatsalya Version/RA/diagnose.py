#!/usr/bin/env python3
"""Diagnostic script to check why documents are failing during ingestion.

Connects to LightRAG storage and shows:
1. Sample error messages from failed documents
2. Qdrant connectivity
3. Ollama server connectivity
4. Configuration status
"""

import asyncio
import sys
from pathlib import Path

from config import QDRANT_URL, WORKING_DIR, EMBED_BACKEND, LLM_MODEL, EMBED_MODEL
from lightrag import LightRAG
from lightrag.base import DocStatus

try:
    import qdrant_client
except ImportError:
    qdrant_client = None

try:
    import requests
except ImportError:
    requests = None


def check_qdrant():
    """Check if Qdrant is reachable."""
    print("\n=== Checking Qdrant ===")
    if not requests:
        print("  ⚠️  requests module not installed, skipping connectivity check")
        return
    try:
        resp = requests.get(f"{QDRANT_URL}/", timeout=5)
        if resp.status_code == 200:
            print(f"  ✓ Qdrant is reachable at {QDRANT_URL}")
            collections = requests.get(f"{QDRANT_URL}/collections", timeout=5).json()
            num_collections = len(collections.get("result", {}).get("collections", []))
            print(f"  ✓ {num_collections} collections exist")
        else:
            print(f"  ✗ Qdrant returned {resp.status_code}: {resp.text}")
    except Exception as e:
        print(f"  ✗ Qdrant unreachable: {e}")


def check_ollama():
    """Check if Ollama servers are reachable."""
    print("\n=== Checking Ollama ===")
    if not requests:
        print("  ⚠️  requests module not installed, skipping connectivity check")
        return
    from multi_ollama import load_servers
    try:
        servers = load_servers()
        for server in servers:
            try:
                resp = requests.get(f"{server}/api/tags", timeout=5)
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    model_names = [m.get("name", "?") for m in models[:3]]
                    print(f"  ✓ {server}: {len(models)} models ({', '.join(model_names)}...)")
                else:
                    print(f"  ✗ {server}: {resp.status_code}")
            except Exception as e:
                print(f"  ✗ {server}: {e}")
    except FileNotFoundError:
        print("  ✗ servers.txt not found")
    except ValueError as e:
        print(f"  ✗ {e}")


def check_config():
    """Print current configuration."""
    print("\n=== Configuration ===")
    print(f"  LLM Model:        {LLM_MODEL}")
    print(f"  Embed Model:      {EMBED_MODEL}")
    print(f"  Embed Backend:    {EMBED_BACKEND}")
    print(f"  Qdrant URL:       {QDRANT_URL}")
    print(f"  Working Dir:      {WORKING_DIR}")
    if Path(WORKING_DIR).exists():
        print(f"  ✓ Working directory exists")
    else:
        print(f"  ✗ Working directory does not exist")


async def check_failed_docs():
    """Show error messages from failed documents."""
    print("\n=== Failed Documents ===")
    if not Path(WORKING_DIR).exists():
        print(f"  ⚠️  {WORKING_DIR} doesn't exist yet (no run completed)")
        return

    try:
        from ingest import build_rag
        rag = await build_rag()

        failed_docs = await rag.get_docs_by_status(DocStatus.FAILED)
        if not failed_docs:
            print("  ✓ No failed documents")
            return

        print(f"  Found {len(failed_docs)} failed document(s)")
        print("\n  Sample errors (first 5):")
        for i, (doc_id, doc_status) in enumerate(list(failed_docs.items())[:5]):
            error = doc_status.error_msg or "(no error message)"
            print(f"    {i+1}. {doc_id}")
            print(f"       Error: {error[:200]}")  # First 200 chars
            print()

        # Check if they all have the same error (systematic issue)
        errors = [d.error_msg for d in failed_docs.values()]
        unique_errors = set(str(e)[:100] for e in errors)  # First 100 chars
        if len(unique_errors) == 1:
            print(f"  ⚠️  ALL failed documents have the SAME error (systematic issue)")
            print(f"      Error: {list(unique_errors)[0]}")
        else:
            print(f"  ✓ Various errors ({len(unique_errors)} different types)")

        await rag.finalize_storages()
    except Exception as e:
        print(f"  ✗ Could not read LightRAG storage: {e}")


async def main():
    print("=" * 80)
    print("LightRAG Ingestion Diagnostics")
    print("=" * 80)

    check_config()
    check_qdrant()
    check_ollama()
    await check_failed_docs()

    print("\n" + "=" * 80)
    print("Next steps:")
    print("  1. If Qdrant/Ollama unreachable: start them (docker start)")
    print("  2. If all errors are the same: check the error message above")
    print("  3. Otherwise: run ./reset.sh and try again")
    print("=" * 80)


if __name__ == "__main__":
    asyncio.run(main())
