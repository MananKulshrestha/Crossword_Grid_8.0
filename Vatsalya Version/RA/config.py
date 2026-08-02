import os

# --- Ollama connection -------------------------------------------------
# Server isn't running yet on this machine; this is just the endpoint the
# scripts will call once it's up.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11345")

# --- Models --------------------------------------------------------------
# Pick one. Default is gemma4:27b per your cluster's 48GB VRAM headroom;
# swap LLM_MODEL to "qwen3.6" if you'd rather use that instead. Whichever
# you use, make sure you `ollama pull <exact-tag>` first -- these are the
# names you gave me, double check the exact tag in `ollama list` once the
# server + models are available, tag naming can differ slightly (e.g.
# "qwen3.6" vs "qwen3.6:latest").
LLM_MODEL = os.environ.get("LIGHTRAG_LLM_MODEL", "gemma4:27b")
EMBED_MODEL = os.environ.get("LIGHTRAG_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768  # nomic-embed-text output size

# Reasoning models (qwen3-family) support a "think" toggle -- forwarded to
# Ollama's chat call so extraction/query calls don't burn time on
# chain-of-thought output we don't need. Harmless no-op for non-thinking
# models like gemma4.
DISABLE_THINKING = True

# Context window sent to Ollama per call. LightRAG's entity-extraction
# system prompt alone runs ~2-3k tokens, plus the chunk text (up to
# CHUNK_TOKEN_SIZE below) and the model's own output. 8192 is the bare
# minimum that won't silently truncate the prompt; 16384 gives headroom
# for gleaning passes and longer outputs. With 48GB VRAM and a 27b model
# this is affordable, so default to the safer value.
NUM_CTX = int(os.environ.get("LIGHTRAG_NUM_CTX", "16384"))

# --- Concurrency ----------------------------------------------------
# Ollama server can serve multiple requests in parallel (set via
# OLLAMA_NUM_PARALLEL on the server side, see README) -- these control how
# many concurrent requests LightRAG issues to it.
LLM_MAX_ASYNC = int(os.environ.get("LIGHTRAG_LLM_MAX_ASYNC", "4"))
EMBEDDING_MAX_ASYNC = int(os.environ.get("LIGHTRAG_EMBEDDING_MAX_ASYNC", "4"))

# --- Paths -----------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_MD = os.path.join(BASE_DIR, "flipkart_lightrag_corpus.md")
SOURCE_JSONL = os.path.join(BASE_DIR, "flipkart_catalog_structured.jsonl")
WORKING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lightrag_storage")

# --- Test batch ---------------------------------------------------------
# Start small -- entity/relation extraction is one LLM call per chunk.
BATCH_SIZE = 150

# --- Entity extraction scope ---------------------------------------------
# LightRAG's default entity extraction is generic (person, organization,
# location, event...) which is the wrong ontology for a product catalog.
# Constrain it to the retrieval-architecture doc's schema so the graph only
# accumulates entity types a shopping query can actually use.
ENTITY_TYPES = [
    "PRODUCT",
    "BRAND",
    "CATEGORY",
    "MATERIAL",
    "OCCASION",
    "STYLE",
]
