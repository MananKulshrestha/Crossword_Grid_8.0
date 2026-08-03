import os

# --- Ollama connection ---------------------------------------------------
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11435")

# --- Models ----------------------------------------------------------------
# gemma4:31b: reliable extraction format-following, but tensor-splits across
# 2 of the cluster's 11GB GPUs (no single card fits it) -- pair with
# LLM_MAX_ASYNC/EMBEDDING_MAX_ASYNC=2 below.
# gemma4:e4b: fits one card, supports higher concurrency. With improved
# prompt clarity (5-field format examples), format errors should be minimal.
LLM_MODEL = os.environ.get("LIGHTRAG_LLM_MODEL", "gemma4:e4b")
EMBED_MODEL = os.environ.get("LIGHTRAG_EMBED_MODEL", "nomic-embed-text")
EMBED_DIM = 768  # nomic-embed-text output size

# "local" runs embedding on this Mac via sentence-transformers/torch
# (local_embed.py) instead of the shared Ollama cluster -- removes
# embedding<->extraction GPU contention entirely (see local_embed.py's
# docstring; this was implicated in embedding-worker timeouts under load).
# "ollama" keeps embedding on the remote server alongside extraction.
EMBED_BACKEND = os.environ.get("LIGHTRAG_EMBED_BACKEND", "local")

DISABLE_THINKING = True  # forwards think:false to Ollama (no-op for non-reasoning models)
# 8192 is the bare minimum that won't risk truncating the ~2-3k token
# extraction system prompt + chunk text + output -- lowered from 16384 to
# shrink per-request KV-cache VRAM, freeing headroom to raise concurrency
# below without changing LLM_MODEL. Don't go lower without checking for
# truncation warnings in the logs.
NUM_CTX = int(os.environ.get("LIGHTRAG_NUM_CTX", "8192"))

# --- Concurrency -----------------------------------------------------------
# History: 8 -> 4 -> 2 -> back to 4. At 8, embedding calls (then still on
# Ollama) missed LightRAG's 60s worker timeout, starved behind concurrent
# gemma4:31b extraction on the same GPUs -- fixed independently by moving
# embedding to EMBED_BACKEND "local" (local_embed.py), off the shared GPUs
# entirely. Separately, `ollama ps` showed the model split "26%/74%
# CPU/GPU" at concurrency=4, which looked like a real VRAM ceiling and
# prompted a drop to 2 -- but that split turned out to be a server-side
# misconfiguration on gnode071, not a genuine capacity limit, so 4 is
# confirmed fine and restored. If a CPU/GPU split in `ollama ps` shows up
# again, check the server config before assuming concurrency needs to drop
# -- it was a false signal last time.
# Must match OLLAMA_NUM_PARALLEL on the Ollama server or requests queue.
LLM_MAX_ASYNC = int(os.environ.get("LIGHTRAG_LLM_MAX_ASYNC", "8"))
# Independent of LLM_MAX_ASYNC now that EMBED_BACKEND defaults to "local" --
# runs on this Mac's CPU/MPS, not the remote GPUs, so it isn't part of the
# same VRAM budget. Only couple it back to LLM_MAX_ASYNC if EMBED_BACKEND
# is switched to "ollama".
EMBEDDING_MAX_ASYNC = int(os.environ.get("LIGHTRAG_EMBEDDING_MAX_ASYNC", "4"))

# --- Paths -------------------------------------------------------------
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE_MD = os.path.join(BASE_DIR, "flipkart_lightrag_corpus.md")
SOURCE_JSONL = os.path.join(BASE_DIR, "flipkart_catalog_structured.jsonl")
WORKING_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "lightrag_storage")

# None = ingest the entire corpus. Resumable across interrupted runs:
# LightRAG's own doc_status tracks PENDING/PROCESSING/FAILED docs and
# apipeline_process_enqueue_documents() (which ainsert() calls) picks them
# back up on the next run -- see IMPLEMENTATION.md #5. This only works if
# you DON'T run reset.sh between runs (that wipes doc_status, so nothing
# is left to resume) and doc_ids stay deterministic (ours already are,
# f"sku-{sku_id}"). Set to an int (e.g. 150) to cap at a test batch instead.
_env_batch_size = os.environ.get("LIGHTRAG_BATCH_SIZE")
BATCH_SIZE = int(_env_batch_size) if _env_batch_size else None

# Constrains LightRAG's entity extraction away from its generic default
# ontology (Person, Organization, Location, Event, Concept, Artifact...) to
# a shopping-catalog schema, AND pushes it toward specific attribute values
# instead of generic ones (e.g. "Round Neck" not "Neckline", "Maroon" not
# "Color"). This is passed as addon_params={"entity_types_guidance": ...} --
# NOT addon_params={"entity_types": [...]}, which this LightRAG version
# silently ignores (no code path reads that key at all; only
# "entity_types_guidance" is read). Passing the wrong key means the model
# falls back to the generic default ontology with no error -- if extraction
# output looks generic/off-topic, check this is actually wired into
# ingest.py's addon_params, not just defined here.
# v2: constrains relationship_keywords to a fixed vocabulary (HAS_BRAND,
# HAS_FEATURE, etc.) on top of the entity-type constraint, and adds
# exclusion/canonicalization/no-inference rules -- condensed rather than
# one bullet + 2-3 examples per type, to leave more of NUM_CTX free for
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
