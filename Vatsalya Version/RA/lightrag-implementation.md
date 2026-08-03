# LightRAG Implementation — Downsampled Graph Construction & Prompt Design

Design document only — nothing here has been implemented or run. It picks up
where `retrieval-architecture.md`, `IMPLEMENTATION.md`, and `plan.md` leave
off, and answers two questions in depth: **how to choose which products get
full LightRAG graph extraction** (instead of the full 20,000-SKU catalog,
which is estimated at ~2.6 days of LLM extraction time), and **how to fix and
improve the entity-extraction prompt** LightRAG actually sends to the LLM.

Everything below is grounded in the real `lightrag-hku` package source
(v1.5.5, the current PyPI release — the same one `requirements.txt`'s
unpinned `lightrag-hku` line would install today) and the real catalog data
in `flipkart_catalog_structured.jsonl`, not general LightRAG documentation or
generic ML advice. Nothing here touches Ollama or requires it to be running.

---

## 1. Executive summary

| Question | Answer |
|---|---|
| Can Qdrant hold embeddings for all 20,000 products while the graph covers only a subset? | **Yes** — LightRAG has a native, documented mechanism for exactly this (`skip_kg` process option), not a workaround. |
| Does LightRAG require the graph and vector store to share the same corpus? | **No** — they are four separate storage objects, populated in separate pipeline stages. |
| Is our current entity-type constraint (`addon_params={"entity_types": ENTITY_TYPES}`) actually working? | **No.** This is a real bug, confirmed by source inspection: the installed LightRAG version does not read an `entity_types` key at all. Every ingested document today would silently fall back to LightRAG's generic default ontology (Person, Location, Event, ...), not our schema. |
| How many products should get full graph extraction? | **2,673 (≈13.4% of the catalog)** — measured, not estimated, by running the real coverage-sampling algorithm; chosen by coverage, not at random — reasoning and the full sizing curve in §5. |

---

## 2. Architecture overview

```mermaid
flowchart TD
    A["flipkart_catalog_structured.jsonl (20,000 SKUs)"] --> B["SQL load: product_metadata (MySQL)"]
    A --> C["flipkart_lightrag_corpus.md (description-only text corpus)"]
    C --> D["BM25 index — full catalog"]
    C --> E["Product-family dedup + coverage sampler (see §5)"]
    E --> F["Representative subset (2,673 SKUs, measured): process_options = normal"]
    E --> G["Remaining SKUs (~18,500-19,200): process_options = '!' (skip_kg)"]
    F --> H["LightRAG apipeline_enqueue_documents + apipeline_process_enqueue_documents"]
    G --> H
    H --> I["Stage 1 (all docs): chunk + embed -> chunks_vdb (Qdrant) — FULL CATALOG"]
    H --> J{"skip_kg set for this doc?"}
    J -- "No (subset)" --> K["Stage 2: LLM entity/relation extraction (custom prompt, see §6)"]
    J -- "Yes (rest)" --> L["Stage 2 skipped — no LLM call"]
    K --> M["Stage 3: merge_nodes_and_edges -> graph + entities_vdb + relationships_vdb — SUBSET ONLY"]
    L --> N["Doc reaches PROCESSED; chunk fully searchable, no graph presence"]
    B --> O["search_catalog: SQL hard filter"]
    D --> P["search_catalog: BM25 candidates"]
    I --> Q["search_catalog: mix-mode raw chunk vector pass — full catalog"]
    M --> R["search_catalog: mix-mode local/global passes — subset only"]
    O & P & Q & R --> S["Candidate fusion & rerank (merge.py, not yet built)"]
```

The key idea, stated once: **SQL, BM25, and Qdrant's chunk embeddings all
stay full-catalog.** Only the graph (entities, relationships, the node/edge
store) is intentionally smaller — and that's a deliberate LightRAG feature,
not a compromise bolted on from outside.

---

## 3. How LightRAG actually separates the graph from the vector store

Confirmed by reading `lightrag.py`, `pipeline.py`, and `operate.py` directly
(not from memory or general docs).

### 3.1 Four storage objects, not one

`LightRAG.__init__` creates:

- `self.chunks_vdb` — vector store of raw chunk-text embeddings
- `self.entities_vdb` — vector store of extracted entity embeddings
- `self.relationships_vdb` — vector store of extracted relationship embeddings
- `self.chunk_entity_relation_graph` — the actual graph (NetworkX by default)

Setting `vector_storage="QdrantVectorDBStorage"` (the Tier 3 plan) points
**all three** vdbs at Qdrant, each as its own namespaced collection. "The
Qdrant vector database" is really three collections; only `chunks_vdb`'s
collection is what gives you flat, full-catalog vector search.

### 3.2 The ingestion pipeline is staged, and stage 2 is skippable

In `pipeline.py`'s document-processing loop:

```
Stage 1 (every document): chunks_vdb.upsert(chunks) + text_chunks.upsert(chunks)   — embedding call only
Stage 2 (every document, UNLESS skipped): LLM entity/relation extraction            — the expensive part
Stage 3 (only if stage 2 ran): merge_nodes_and_edges(...) → graph + entities_vdb + relationships_vdb
```

Stage 2 has a **first-class, documented skip flag** — a per-document
`process_options` character, literally `"!"`
(`PROCESS_OPTION_SKIP_KG` in `constants.py`). The code comment at the skip
branch says exactly what we want:

> *"skip extraction entirely; chunks remain in the vector store so naive /
> mix retrieval still works."*

When set: no LLM call happens for that document, `merge_nodes_and_edges` is
never called for it, but it's still fully embedded and searchable via
`chunks_vdb`.

**One real implementation detail for later:** the convenience method our
`ingest.py` currently calls, `rag.ainsert(text, ids=sku_id)`, does **not**
expose `process_options` — its own docstring says so explicitly ("process_options
is intentionally not passed"). Using `skip_kg` means calling the two
lower-level methods it wraps directly:
`apipeline_enqueue_documents(docs, ids=sku_ids, process_options=[...])` then
`apipeline_process_enqueue_documents()`. Not a blocker — just a concrete
`ingest.py` change for whenever this gets implemented.

### 3.3 Query-time confirmation

In `operate.py`, `mode="mix"` runs three passes: `local` (reads
`entities_vdb`), `global` (reads `relationships_vdb`), and — confirmed at the
call site — a **separate raw vector pass straight over `chunks_vdb`**,
independent of the graph. This is what makes full-catalog vector search
actually deliverable under a downsampled graph: every SKU has a `chunks_vdb`
entry regardless of `skip_kg`, so the raw-vector pass always has full
coverage.

---

## 4. What's gained and lost by downsampling the graph

| | Full catalog through `chunks_vdb` | Subset only, through graph |
|---|---|---|
| SQL hard filter | Full catalog (unaffected — separate branch) | — |
| BM25 | Full catalog (unaffected — separate branch) | — |
| Raw vector similarity (`mix` mode's 3rd pass, and plain `naive` mode) | **Full catalog** — every SKU has a chunk embedding | — |
| Entity-level match (`local` mode: "cotton," a specific brand node) | Not available outside the subset | **Subset only** |
| Relationship/thematic match (`global` mode: "similar brand," "pairs with this") | Not available outside the subset | **Subset only** |
| Graph traversal (multi-hop: brand → category → related products) | Not available outside the subset | **Subset only** |

This is not a hidden inconsistency — nothing returns wrong data or silently
disagrees. It's a real, bounded capability gradient: subset SKUs get
graph-enhanced retrieval; the rest get full vector-only semantic retrieval,
on top of SQL and BM25 which already cover 100% of the catalog regardless. A
`SAME_BRAND_AS`-style edge fundamentally requires ≥2 products of that brand
to exist at all — a singleton-brand product could never produce one no
matter which tier it's ingested at, so it loses nothing structurally by being
in the "rest" group.

---

## 5. Choosing the subset by information coverage, not by percentage

### 5.1 What actually contributes new graph information

`merge_nodes_and_edges` merges same-named entities across documents into
**one node with combined evidence** — it does not create duplicate nodes.
Consequence: the marginal graph value of the *N*th product mentioning a
brand/category/material the graph already has a node for is small (one more
low-weight edge into an already-saturated node). The graph's real
information content is bounded by:

- how many **distinct entity names** exist (distinct brands, categories,
  materials, occasion/style terms actually mentioned), and
- how many **distinct relationship pairs** get formed between them.

So: redundant products are ones whose brand/category/material is already
well-represented and whose description doesn't introduce new occasion/style
language. High-value products introduce a not-yet-seen combination, or a
brand that spans multiple categories (a relational hub).

### 5.2 Entity types vs. what's actually in the structured metadata

| Entity type | Structured column? | Real cardinality in `flipkart_catalog_structured.jsonl` |
|---|---|---|
| `CATEGORY` | Yes — `category` | 87 distinct values (clean, low-cardinality) |
| — | `subcategory_path` (finer-grained, not its own entity type) | 6,233 distinct paths, avg 3.2 products/path — **too fragmented to target directly**, used only as a within-category diversity tie-breaker |
| `BRAND` | Yes — `brand` | 4,858 distinct, but **2,595 (53%) appear exactly once**. A singleton brand can never form a `SAME_BRAND_AS` edge — target only brands with ≥3 SKUs: **1,448 brands, covering 78.9% of the whole catalog** |
| `MATERIAL` | Yes — `material` | 624 distinct raw strings, but only **99 have ≥10 SKUs**, covering 9,516 of the ~10,587 non-empty records (9,413 of 20,000 have no material at all). The long tail is mostly spelling-variant noise, not real signal |
| `OCCASION` | **No structured column** | Purely emergent from free text — LightRAG's extraction is the only source |
| `STYLE` | **No structured column** | Same — purely emergent from free text |
| `PRODUCT` | Implicit (one per SKU) | 20,000, but see the dedup step below |

`OCCASION`/`STYLE` are the one real gap in a pure-structured-metadata
approach — 4 of 6 entity types map cleanly to columns, 2 don't. The fix,
still without running an LLM: a **cheap keyword-presence regex scan** over
the description text, purely as a *sampling* signal (not for insertion) — a
small fixed vocabulary (occasion: "wedding," "festive," "party," "formal,"
"casual," "office," "daily wear"...; style: "solid," "printed," "slim fit,"
"traditional," "western," "designer"...), flagging which bucket(s) each
product's description touches.

### 5.3 The redundancy problem, measured

A simple normalization (strip color words from `product_name`, group by
brand + category + normalized name) run against the real corpus:

- **20,000 SKUs collapse to 11,606 distinct "product families."** 9,055 are
  true singletons; the rest are variant clusters.
- The clusters are large: **229 SKUs** are all "TheLostPuppy back cover for
  Apple iPad Air" (different print designs only), 88 are "DailyObjects back
  cover for Apple iPad," 66 are "Voylla metal alloy necklace," 58 are
  "HomeeHub polyester eyelet door curtain."

These clusters are almost pure redundancy for the graph — after the first
exemplar, the rest reinforce already-known Brand/Category/Material nodes
with near-identical description phrasing. **Family-level dedup is step
zero**, not an afterthought — it likely halves the effective candidate pool
before any coverage optimization even starts.

### 5.4 The sampling algorithm

```mermaid
flowchart TD
    A["20,000 raw SKUs"] --> B["Normalize product_name: strip color/size/pack-count tokens"]
    B --> C["Group by (brand, category, normalized name) -> ~11,606 families"]
    C --> D["Pick 1-2 exemplars per family (prefer richer/longer description -> more entities per LLM call)"]
    D --> E["Build coverage target set"]
    E --> E1["87 categories"]
    E --> E2["~1,448 brands with >= 3 SKUs"]
    E --> E3["~99 materials with >= 10 SKUs"]
    E --> E4["~30-40 occasion/style keyword buckets (regex heuristic)"]
    E1 & E2 & E3 & E4 --> F["Greedy weighted set-cover: repeatedly pick the family covering the most currently-uncovered targets"]
    F --> G{"Target reached 2-3x, or marginal gain below threshold?"}
    G -- "No" --> F
    G -- "Yes" --> H["Stop; enforce a minimum per-category floor (5-10 products) as top-up"]
    H --> I["Final subset: 2,673 products, measured -> full graph extraction (process_options = normal)"]
    I --> J["Everyone else -> process_options = '!' (skip_kg), chunk-embedded only"]
```

Steps, in words:

1. **Collapse to product families** (§5.3). Represent each family by 1–2
   exemplars, preferring the richest/longest description among near-
   duplicates — a thin description extracts fewer entities regardless of
   which exemplar is chosen, so spend the extraction budget on the most
   informative one.
2. **Build the coverage target set** entirely from structured metadata plus
   the cheap keyword scan (§5.2): 87 categories + ~1,448 brands (≥3 SKUs) +
   ~99 materials (≥10 SKUs) + ~30–40 occasion/style buckets ≈ **~1,670
   distinct coverage targets**.
3. **Greedy weighted set-cover over families**, not random sampling:
   repeatedly pick the not-yet-chosen family whose (category, brand,
   material, occasion/style-buckets) tuple covers the most currently
   *uncovered* targets. Each product typically covers several targets at
   once (one category + one brand + one material + maybe one occasion/style
   hit), so this converges much faster than a naive per-target sum.
4. **Require a small multiplicity (2–3) per target that needs to support a
   relationship** (a `SAME_BRAND_AS`/`SIMILAR_TO` edge needs ≥2 products),
   but stop adding exemplars to a target once it's hit that floor —
   diminishing returns past ~3.
5. **Enforce a per-category floor** independent of the greedy pass (e.g.
   minimum 5–10 products per category), so a big category like Clothing
   (6,222 SKUs) doesn't crowd out a small one like Baby Care (483 SKUs) down
   to near-zero. A category with 1 representative product has no internal
   relational richness at all.

### 5.5 Sizing estimate — reasoned from the vocabulary, not a percentage rule

Target-set size ≈ 87 + 1,448 + 99 + ~35 ≈ **~1,670 distinct coverage
targets**. If every product covered exactly one target with zero overlap,
you'd need ~1,670 × 2–3 exemplars ≈ 3,300–5,000 products — but real products
are multi-target (one product simultaneously covers a category, a brand, a
material, and possibly an occasion/style bucket), so a real greedy set-cover
converges well below that naive sum; this is standard behavior for weighted
set-cover on multi-attribute items — early picks are cheap because they
knock out several targets at once, and the marginal-gain curve flattens hard
well before the arithmetic sum is reached.

**Original estimate (pre-implementation): roughly 800–1,500 products
(≈4–7.5%).** This section's algorithm has since been implemented and run
against the real catalog (`graph_sampling.py`, see §7) — the measured result
refines this estimate rather than confirming it exactly.

**Measured result: the greedy selector naturally saturates at 2,673 products
(≈13.4% of the 20,000-SKU catalog)** — i.e. it stops itself (no remaining
candidate adds any new coverage) at that point, not at an artificial budget
cutoff. At that size: **86/86 categories, 1,448/1,448 brands, 97/99
materials, 20/20 occasion keywords, 18/18 style keywords** are covered at
the required multiplicity.

The original 800–1,500 estimate undershot for a specific, measured reason:
**brands are by far the largest target set** (1,448, vs. 86 categories and
99 materials), so they dominate the total budget needed. A budget sweep on
the real data makes the curve explicit:

| Budget | Chosen | Brand coverage |
|---|---|---|
| 1,500 | 1,500 | 926/1,448 (64%) |
| 2,000 | 2,000 | 1,173/1,448 (81%) |
| 2,500 | 2,500 | 1,386/1,448 (96%) |
| 2,658–3,000 | 2,673 (natural stop) | 1,448/1,448 (100%) |

Categories, materials, and occasion/style all saturate well before brands
do, since there are far fewer of them — so the binding constraint on subset
size is brand coverage specifically, not the target set as a whole. Still
far below a generic "10–30%" rule of thumb, and for the same underlying
reason as before: repeat mentions of an already-known entity merge into the
same node, so coverage saturates at the vocabulary size, not the row count.
The 2 uncovered materials are a genuine, benign edge case (verified): each
has all of its real-catalog occurrences collapse into a single product
family after dedup (near-duplicate color/size variants of one base product),
so no second exemplar exists to reach the multiplicity-2 requirement,
regardless of budget.

---

## 6. Prompt design — a real bug, and how to fix it

### 6.1 The current config does not do what it says

`RA/config.py` defines:

```python
ENTITY_TYPES = ["PRODUCT", "BRAND", "CATEGORY", "MATERIAL", "OCCASION", "STYLE"]
```

and `RA/ingest.py` passes it as `addon_params={"entity_types": ENTITY_TYPES}`.
`IMPLEMENTATION.md` describes this as constraining LightRAG's extraction to
our schema instead of its generic default ontology.

**This does not work with the installed LightRAG version.** A full-package
search of `lightrag-hku` 1.5.5's source for the string `"entity_types"`
(the plain-list key, as opposed to `"entity_types_guidance"`) returns **zero
matches, anywhere in the library**. There is no code path that reads this
key. Because it's an unrecognized dict key, nothing raises — it's silently
ignored, and extraction falls back entirely to LightRAG's **built-in generic
guidance**:

```
Person, Creature, Organization, Location, Event, Concept, Method, Content,
Data, Artifact, NaturalObject
```

If `ingest.py` were run against Ollama today, exactly as currently written,
the graph would fill with generic node types, not `PRODUCT`/`BRAND`/
`CATEGORY`/`MATERIAL`/`OCCASION`/`STYLE` — silently defeating the entire
point of the entity-schema design in `retrieval-architecture.md`. This is
the single most important finding in this document: **it's a latent bug
waiting to trigger the moment Ollama comes back**, not a nice-to-have
polish item.

(This is plausibly a version drift issue — `requirements.txt` pins
`lightrag-hku` with no version, so whatever gets installed today is whatever
LightRAG's API looks like *today*, which may not match whatever version this
code was originally written against.)

### 6.2 The mechanism that actually works

Confirmed in `prompt.py`'s `resolve_entity_extraction_prompt_profile`:
LightRAG resolves the extraction prompt's entity-type guidance from, in
priority order:

1. `addon_params["entity_types_guidance"]` — a full descriptive text block,
   inlined directly.
2. `addon_params["entity_type_prompt_file"]` — a YAML file name, sandboxed
   to `{PROMPT_DIR}/entity_type/*.yml` (`PROMPT_DIR` defaults to
   `./prompts`), containing `entity_types_guidance` (string),
   `entity_extraction_examples` (list of strings, text-tuple mode — the
   mode our config uses by default, since `ENTITY_EXTRACTION_USE_JSON` isn't
   set), and optionally `entity_extraction_json_examples`.
3. Otherwise, the generic default guidance above.

There is no automatic conversion from a bare type-name list into guidance
text — whoever fixes this needs to write the actual descriptive guidance
(one line per type, similar in shape to the generic default's per-type
descriptions), and should package it via `entity_type_prompt_file` (a
checked-in YAML file) rather than a giant inline string in `addon_params`,
since the YAML path is validated and versionable.

### 6.3 The examples problem is separate, and just as real

Even setting aside the broken key, the **default worked example is not a
worked example at all** — `PROMPTS["entity_extraction_examples"]` is
literally just the output syntax template with angle-bracket placeholders:

```
entity<|#|><entity_name><|#|><entity_type><|#|><entity_description>
relation<|#|><source_entity><|#|><target_entity><|#|><relationship_keywords><|#|><relationship_description>
<|COMPLETE|>
```

This teaches the model the *format*, but gives it **zero domain content** to
imitate. Combined with the generic-guidance bug, the model currently has
neither the right vocabulary nor a concrete pattern for what a good
extraction looks like on this kind of text — which is why a real, filled-in,
domain-specific example (via the same `entity_type_prompt_file` YAML,
`entity_extraction_examples` key) matters as much as fixing the type list.

### 6.4 Worked example — same real product, default prompt vs. fixed prompt

Real description text, `SRTEH2FF9KEDEFGF` ("Alisha Solid Womens Cycling
Shorts," already used in `tests/test_bm25.py`):

> Key Features of Alisha Solid Womens Cycling Shorts Cotton Lycra Navy,
> Red, Navy, Specifications of Alisha Solid Womens Cycling Shorts Shorts
> Details Number of Contents in Sales Package Pack of 3 Fabric Cotton Lycra
> Type Cycling Shorts General Details Pattern Solid Ideal For Womens Fabric
> Care Gentle Machine Wash in Lukewarm Water, Do Not Bleach Additional
> Details Style Code ALTHT_3P_21 In the Box 3 shorts

**Today's actual behavior** (generic guidance + placeholder-only example —
what the code currently produces, not a hypothetical): the model has to
force this into Person/Creature/Organization/Location/Event/Concept/Method/
Content/Data/Artifact/NaturalObject. A plausible real output:

```
entity<|#|>Alisha<|#|>Organization<|#|>A brand associated with the product.
entity<|#|>Alisha Solid Womens Cycling Shorts<|#|>Artifact<|#|>A physical clothing item.
entity<|#|>ALTHT_3P_21<|#|>Data<|#|>A style code identifier.
relation<|#|>Alisha Solid Womens Cycling Shorts<|#|>Alisha<|#|>associated with<|#|>The product is linked to this organization.
<|COMPLETE|>
```

Nothing here is typed `BRAND`, `PRODUCT`, `MATERIAL`, or `STYLE` — because
those types don't exist in the guidance the model was actually given. "Cotton
Lycra" and "Solid" likely get dropped entirely (no generic bucket fits them
well, and the model is told to prefer dropping over misclassifying). Every
downstream assumption in `retrieval-architecture.md` — `local` mode matching
on a `MATERIAL` node, `addon_params`'s stated goal of a `BRAND`/`CATEGORY`
graph — silently has nothing to match against.

**With a fixed `entity_types_guidance`** (one line per type, matching the
schema table in `retrieval-architecture.md`) **and a real worked example**
(this exact product, filled in, not placeholders):

```
entity<|#|>Alisha<|#|>BRAND<|#|>Alisha is the brand manufacturing the Solid Womens Cycling Shorts product.
entity<|#|>Alisha Solid Womens Cycling Shorts<|#|>PRODUCT<|#|>A women's cycling shorts product sold in a pack of 3, made of Cotton Lycra fabric with a solid pattern.
entity<|#|>Cycling Shorts<|#|>CATEGORY<|#|>The product category for shorts designed for cycling activity.
entity<|#|>Cotton Lycra<|#|>MATERIAL<|#|>The fabric blend used to manufacture the shorts.
entity<|#|>Solid<|#|>STYLE<|#|>The pattern style of the shorts — a plain, single-color design.
relation<|#|>Alisha Solid Womens Cycling Shorts<|#|>Alisha<|#|>brand, manufacturer<|#|>Alisha Solid Womens Cycling Shorts is manufactured by the brand Alisha.
relation<|#|>Alisha Solid Womens Cycling Shorts<|#|>Cotton Lycra<|#|>made of, fabric<|#|>The shorts are made of Cotton Lycra fabric.
relation<|#|>Alisha Solid Womens Cycling Shorts<|#|>Solid<|#|>has style, pattern<|#|>The shorts have a solid pattern style.
relation<|#|>Alisha Solid Womens Cycling Shorts<|#|>Cycling Shorts<|#|>category of, type<|#|>The product belongs to the cycling shorts category.
<|COMPLETE|>
```

Note there's deliberately **no `OCCASION` entity** in this example — this
particular description doesn't mention one, and that's correct behavior, not
a gap: not every product should yield all six types, and an example that
padded in a fake occasion would teach the model to hallucinate one on every
product.

```mermaid
flowchart TD
    A["addon_params passed to LightRAG(...)"] --> B{"entity_types_guidance key present?"}
    B -- "Yes" --> C["Use custom guidance text directly"]
    B -- "No" --> D{"entity_type_prompt_file key present?"}
    D -- "Yes" --> E["Load YAML from PROMPT_DIR/entity_type/*.yml"]
    E --> F["entity_types_guidance + entity_extraction_examples from file"]
    D -- "No (today's actual config)" --> G["Fall back to built-in generic guidance: Person/Location/Event/Artifact/... — WRONG for this catalog"]
    C --> H["Prompt sent to LLM per chunk"]
    F --> H
    G --> H
    H --> I["Stage 2 extraction output -> merge_nodes_and_edges -> graph"]
```

### 6.5 What to actually change (when this gets picked up)

- Drop `addon_params={"entity_types": ENTITY_TYPES}` from `ingest.py` — it
  does nothing today and is misleading to read.
- Write a real `entity_types_guidance` block, one line per type (`PRODUCT`,
  `BRAND`, `CATEGORY`, `MATERIAL`, `OCCASION`, `STYLE`), each with a short
  description and maybe a keyword hint (occasion/style words to look for),
  matching the shape of the built-in default's per-type lines.
- Write 2–3 real worked examples (not just the one above) covering different
  product shapes from the catalog — at minimum one that *does* have a clear
  occasion/style signal (so the model sees a positive example of those two
  types, not just their absence), and one from a very different category
  (e.g. Jewellery or Home Furnishing) so the examples aren't all clothing.
- Package both as a YAML file under `prompts/entity_type/` and reference it
  via `addon_params={"entity_type_prompt_file": "<name>.yml"}` — versionable,
  reviewable, and validated by LightRAG itself at load time (it raises
  clearly on a malformed profile), rather than an inline string easy to
  silently typo.

None of this requires Ollama to write or review — only to actually run and
observe the extraction output, which stays blocked until the cluster's back.

---

## 7. Status and next steps

1. **Family-dedup + coverage-sampling script (§5) — built and tested.**
   `graph_sampling.py` implements normalization, family-dedup, target-registry
   construction, greedy set-cover, and category-floor top-up exactly as
   designed in §5.4; `tests/test_graph_sampling.py` (6 tests, real catalog,
   no mocks) passes. Output: `graph_sampling_output/full_extraction_skus.txt`
   (2,673 SKU IDs — the ones that should get full LightRAG extraction) and
   `graph_sampling_output/coverage_report.json` (coverage stats per target
   type, for review). Not yet committed — pending your teammate's sign-off
   on the approach before pushing.
2. Occasion/style keyword vocabulary (§5.2) — done as part of step 1
   (`OCCASION_KEYWORDS` / `STYLE_KEYWORDS` in `graph_sampling.py`); both hit
   100% coverage on the real corpus.
3. `entity_types_guidance` + worked examples (§6.5) — not yet started.
   Content can be drafted and reviewed now; can't be *validated* (does it
   actually produce the right entity types) until Ollama is back.
4. `ingest.py` changes to use `apipeline_enqueue_documents` with per-document
   `process_options`, reading `graph_sampling_output/full_extraction_skus.txt`
   to decide `""` vs `"!"` per SKU (§3.2) — not yet started. Small,
   mechanical change, blocked on Ollama only for testing, not for writing.

Steps 1–2 are done; step 3 can proceed in parallel without Ollama; step 4 is
mechanical once steps 1 and 3 both land.
