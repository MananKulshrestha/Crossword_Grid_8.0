import os

# --- Ollama connection -------------------------------------------------
# Used for embedding calls only. LLM extraction calls (the expensive,
# GPU-bound part) instead round-robin across every server listed in
# servers.txt via multi_ollama.py -- see ingest.py's build_rag() and
# servers.txt's own comments. OLLAMA_HOST is still the fallback/default
# single-server value servers.txt ships with, and remains what embedding
# uses regardless of how many extraction servers are configured.
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11345")

# --- Models --------------------------------------------------------------
# gemma4:27b: reliable extraction format-following, but tensor-splits
# across multiple GPUs (no single card fits it).
# gemma4:e4b: fits one card, supports higher concurrency -- default here
# since extraction now round-robins across servers.txt, which favors a
# model that fits on a single GPU per server.
# Swap LLM_MODEL to "qwen3.6" if you'd rather use that instead. Whichever
# you use, make sure you `ollama pull <exact-tag>` first on every server in
# servers.txt -- double check the exact tag in `ollama list`, tag naming
# can differ slightly (e.g. "qwen3.6" vs "qwen3.6:latest").
LLM_MODEL = os.environ.get("LIGHTRAG_LLM_MODEL", "gemma4:e4b")
EMBED_MODEL = os.environ.get("LIGHTRAG_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768  # nomic-embed-text output size

# "local" runs embedding on this machine via sentence-transformers/torch
# (local_embed.py, Apple Silicon MPS if available else CPU) instead of the
# Ollama cluster -- removes embedding<->extraction GPU contention entirely
# and doesn't need OLLAMA_HOST to serve EMBED_MODEL at all. "ollama" keeps
# embedding on OLLAMA_HOST alongside (or separate from) extraction.
EMBED_BACKEND = os.environ.get("LIGHTRAG_EMBED_BACKEND", "local")

# Reasoning models (qwen3-family) support a "think" toggle -- forwarded to
# Ollama's chat call so extraction/query calls don't burn time on
# chain-of-thought output we don't need. Harmless no-op for non-thinking
# models like gemma4.
DISABLE_THINKING = True

# Context window sent to Ollama per call. LightRAG's entity-extraction
# system prompt alone runs ~2-3k tokens, plus the chunk text (up to
# CHUNK_TOKEN_SIZE below) and the model's own output. 8192 is the bare
# minimum that won't silently truncate the prompt -- don't go lower
# without checking for truncation warnings in the logs.
NUM_CTX = int(os.environ.get("LIGHTRAG_NUM_CTX", "8192"))

# --- Concurrency ----------------------------------------------------
# Ollama server can serve multiple requests in parallel (set via
# OLLAMA_NUM_PARALLEL on the server side, see README) -- these control how
# many concurrent requests LightRAG issues in total. LLM_MAX_ASYNC's
# requests are distributed round-robin across every server in servers.txt
# (multi_ollama.py), so with N servers each server sees roughly
# LLM_MAX_ASYNC / N concurrent requests -- raise LLM_MAX_ASYNC as servers
# are added, don't leave it sized for a single server. EMBEDDING_MAX_ASYNC
# is unaffected by servers.txt -- embedding always targets OLLAMA_HOST alone.
LLM_MAX_ASYNC = int(os.environ.get("LIGHTRAG_LLM_MAX_ASYNC", "4"))
# With EMBED_BACKEND="local", concurrent encode() calls contend for the
# same CPU/MPS device rather than parallelizing (see local_embed.py) --
# this is serialized internally regardless of this value in that mode.
# Only matters for EMBED_BACKEND="ollama", where it should match
# OLLAMA_NUM_PARALLEL on OLLAMA_HOST.
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
# -- there is NO addon_params["entity_types"] key anywhere in lightrag-hku
# (confirmed by a full-package source search). An earlier version of this
# file passed exactly that non-existent key, which LightRAG silently
# ignored -- every extraction would have silently fallen back to the
# generic default ontology. Passing "entity_types_guidance" directly (not
# via an entity_type_prompt_file YAML profile -- that indirection added a
# PROMPT_DIR/file-resolution path with no benefit over an inline string
# here) is what ingest.py's build_rag() actually wires into addon_params.
#
# Constrains relationship_keywords to a fixed vocabulary (HAS_BRAND,
# HAS_FEATURE, etc.) on top of the entity-type constraint, and constrains
# the model to a strict 5-field relationship format -- condensed rather
# than one bullet + example per type, to leave more of NUM_CTX free for
# actual chunk text. "Feature" is the catch-all entity type for anything
# not covered by a more specific type (fit, neckline, sleeve, pattern,
# care instructions, size, ...) -- keep it granular in entity_type/
# entity_description even though its relationship_keywords is always
# HAS_FEATURE; the fixed relation vocabulary is what downstream queries
# filter on, entity_type is what a human/UI reads.
ENTITY_TYPES_GUIDANCE = """Extract product facts only. Entity types: Product, Brand, Category, Material, Color, Feature, Technology, CompatibleItem, Audience, Certification, Warranty. Extract specific values ("Round Neck" not "Neckline"). Skip price, weight, SKUs, dimensions.

RELATIONSHIP FORMAT: Output exactly 5 fields, no more, no less.
["source", "target", "keyword", "description", weight]

keyword MUST be ONE of: HAS_BRAND, HAS_CATEGORY, HAS_MATERIAL, HAS_COLOR, HAS_FEATURE, USES_TECHNOLOGY, COMPATIBLE_WITH, TARGETED_AT, HAS_CERTIFICATION, HAS_WARRANTY

description: 1-2 words only ("cotton material", "navy color")
weight: 0.9 (certain) or 0.7 (inferred) ONLY

Correct: ["Cotton", "Shirt", "HAS_MATERIAL", "material type", 0.9]
Wrong: ["Cotton", "Shirt", "HAS_MATERIAL", "material type", 0.9, "extra"]

Use canonical names. Extract stated facts only."""

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
