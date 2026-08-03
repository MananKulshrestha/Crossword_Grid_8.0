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

# --- Qdrant connection -------------------------------------------------
# LightRAG's QdrantVectorDBStorage reads its connection straight from the
# QDRANT_URL / QDRANT_API_KEY environment variables at storage-initialize
# time (lightrag/kg/qdrant_impl.py) -- it does NOT take these as LightRAG(...)
# constructor kwargs. ingest.py sets os.environ.setdefault(...) from this
# value before constructing LightRAG, so an already-exported QDRANT_URL
# (e.g. a teammate's own instance) is respected, and this is just the
# fallback default for the local Docker container (see README.md).
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")

# --- Entity-type extraction prompt --------------------------------------
# LightRAG's default entity extraction is generic (person, organization,
# location, event...) which is the wrong ontology for a product catalog.
# The only override LightRAG actually reads is addon_params["entity_types_guidance"]
# (a descriptive text block) or addon_params["entity_type_prompt_file"] (a
# YAML profile combining guidance + worked examples) -- there is NO
# addon_params["entity_types"] key anywhere in lightrag-hku (confirmed by a
# full-package source search, see lightrag-implementation.md section 6.1).
# An earlier version of this file passed exactly that non-existent key,
# which LightRAG silently ignored -- every extraction would have silently
# fallen back to the generic default ontology. Fixed by using the real
# mechanism: a YAML profile under PROMPT_DIR/entity_type/, referenced from
# ingest.py via addon_params={"entity_type_prompt_file": "ecommerce_catalog.yml"}.
#
# PROMPT_DIR must be absolute (not LightRAG's relative "./prompts" default)
# so this resolves correctly regardless of the working directory ingest.py
# is launched from.
PROMPT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")
ENTITY_TYPE_PROMPT_FILE = "ecommerce_catalog.yml"

# --- Paths -----------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_MD = os.path.join(BASE_DIR, "flipkart_lightrag_corpus.md")
SOURCE_JSONL = os.path.join(BASE_DIR, "flipkart_catalog_structured.jsonl")
WORKING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lightrag_storage")

# graph_sampling.py's output: which SKUs get full LightRAG entity/relation
# extraction (process_options="") vs. chunk-embedding-only (process_options="!",
# LightRAG's native skip_kg flag). Run graph_sampling.py to (re)generate this
# before running ingest.py -- see lightrag-implementation.md section 5.
GRAPH_SAMPLING_SUBSET_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "graph_sampling_output", "full_extraction_skus.txt"
)

# --- Batch size -----------------------------------------------------------
# None (default) processes the entire catalog: every SKU gets chunk-embedded
# into Qdrant, and GRAPH_SAMPLING_SUBSET_PATH decides which ones additionally
# get full graph extraction. Override with LIGHTRAG_BATCH_SIZE for a quick
# smoke test -- but note a small batch takes documents in *file order*, which
# will under-sample the coverage-chosen full-extraction subset (that subset
# is scattered across the catalog by design, not concentrated up front), so
# a smoke test's "chosen for full extraction" count will look low. That's
# expected for a partial run, not a bug.
_batch_size_env = os.environ.get("LIGHTRAG_BATCH_SIZE", "").strip()
BATCH_SIZE = int(_batch_size_env) if _batch_size_env else None
