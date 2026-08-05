# Deployment Plan — RA Retrieval Subsystem on DigitalOcean

Answers to the team's open questions, grounded in the current codebase (no
hypothetical future architecture), followed by a concrete deployment plan.
**No code has been changed to produce this document.**

---

## 1. What is the cleanest deployment architecture?

Given the explicit constraint (minimal changes, no database migration, no
retrieval-logic changes), the cleanest architecture is a **lift-and-shift
of the exact local dependency stack** onto DigitalOcean, plus one small,
purely additive piece (a thin HTTP wrapper — see §2). Four logical
components, each already independently defined by the existing code and
docs:

1. **MySQL** — same schema, same data, same connection pattern
   (`sql_filter.py`'s `_connect()`/env vars), just hosted instead of local.
2. **Qdrant** — the exact `docker run qdrant/qdrant` setup `RA/README.md`
   already documents, just on a DO Droplet instead of a laptop.
3. **The RA Python app** (`sql_filter.py`, `bm25_index.py`,
   `semantic_search.py`, `merge.py`, `search_catalog.py`) — unchanged
   code, running on a Droplet with `lightrag_storage/` on a persistent
   disk, pointed at (1) and (2) via existing env vars
   (`FLIPKART_DB_HOST`, `QDRANT_URL`).
4. **Ollama** — the one piece with no clean existing "just host it" story
   (see §5, §8).

Nothing here requires touching `merge.py`, `search_catalog.py`,
`semantic_search.py`, `sql_filter.py`, or `bm25_index.py` — they're
already written to read connection details from environment variables with
local defaults, which is exactly what a deployment needs.

---

## 2. HTTP service or importable library?

**Recommendation: HTTP service.** Reasoning, grounded in what's actually
been said and what's actually in the code:

- Manan's stated reason for wanting DigitalOcean is explicit: *"requiring
  every developer to set up MySQL, Qdrant, LightRAG, Ollama, Docker, etc.
  locally makes testing difficult."* An importable Python library does
  **not** solve this — every developer would still need network-reachable
  MySQL/Qdrant credentials, a working Python env with `sentence-transformers`/
  `lightrag-hku`/`qdrant-client` installed, and Ollama reachable, on their
  own machine, to import and call `search_catalog()` directly. Only
  exposing it as a network service actually removes the "everyone needs
  the full stack locally" requirement.
- This also directly resolves the **"packaging boundary"** question
  already flagged as open in `RA/AGENTIC_INTEGRATION.md` §7 — how
  `chat-agentic-docs` would import RA's code at all. An HTTP endpoint
  sidesteps that entirely: no cross-repo Python import, no shared package,
  just a URL — and it's the more natural fit for the cart subsystem's own
  precedent of a real, external-store-backed adapter
  (`DatabaseCartAdapter`) implementing a Port directly against a network
  service.
- **Open item, don't guess:** George's comment ("hosted retrieval system,
  share a URL") is ambiguous between "a hosted retrieval **API**" and
  "just a hosted **database**." If George meant only a hosted database
  (MySQL/Qdrant reachable remotely, but RA's Python code still runs
  per-developer), that does **not** solve Manan's stated problem — each
  dev would still need Ollama, `lightrag-hku`, `sentence-transformers`,
  etc. installed locally. This is worth a direct 2-line clarification with
  George before committing to one architecture, since the two readings
  lead to genuinely different deployments.

The HTTP wrapper itself is new code (not yet written — see §7's plan) but
is strictly additive: a new file exposing `search_catalog()` over a
`POST /search` endpoint, without modifying `search_catalog.py` or anything
it calls.

---

## 3. What components need to be deployed?

| Component | Exists today? | Deploy as |
|---|---|---|
| MySQL (`product_metadata`) | Yes, local Docker | DO Droplet (same `docker run`) or DO Managed MySQL |
| Qdrant | Yes, local Docker, **but see §4 — currently empty of real data** | DO Droplet, same `docker run qdrant/qdrant` command `README.md` already documents, + a DO Volume for `/qdrant/storage` |
| `lightrag_storage/` (graph + KV + doc-status) | **No — doesn't exist locally either**, see §4 | DO Volume mounted at `WORKING_DIR`, on the same Droplet as the RA app |
| BM25 index (`bm25_storage/`) | Exists locally now (built during this session's test run, 8.8MB, full 19,996-doc catalog) but **gitignored, not part of `backup.sh`** | Rebuilds automatically on first run against the committed corpus file — no LLM needed, seconds of work; don't bother shipping it, just let it rebuild |
| `graph_sampling_output/` (coverage subset) | Yes, **committed to git** (`full_extraction_skus.txt`, `coverage_report.json`) | Already present wherever the repo is checked out — nothing to deploy separately |
| The RA Python app | Yes, local only | DO Droplet, `uv sync`/`pip install -r requirements.txt`, env vars pointed at the above |
| Ollama | Yes, local only | Open decision — see §5, §8 |
| `sentence-transformers` model weights (local embedding + CrossEncoder) | Downloaded on first local use | Same — downloads on first run on the Droplet; budget for that cold-start cost (see §5) |

---

## 4. Restore from backup vs. rebuild — the actual current state

I checked this directly rather than assuming:

- **`Vatsalya Version/RA/backups/` is empty** (only a stray `.DS_Store`) —
  **there is no existing backup zip to restore from, anywhere, right now.**
- **`lightrag_storage/` doesn't exist locally either** (only two log files
  from one 500-doc smoke-test run survive, per earlier investigation in
  this project). Qdrant, wherever it's currently running, is either empty
  or holds only that same partial smoke-test data.
- **`backup.sh`/`import.sh` are real, working scripts** for what they
  cover (`lightrag_storage/`, `graph_sampling_output/`, `config.py`,
  `servers.txt`, and Qdrant's data via `docker exec ... tar` of the named
  volume) — but they only back up what exists, and right now that's
  nothing meaningful.
- **MySQL is not part of `backup.sh`/`import.sh` at all** — its data
  lives entirely in the committed `Vatsalya Version/flipkart_metadata.sql`
  file, loaded fresh via `mysql < flipkart_metadata.sql` (per
  `Vatsalya Version/README.md`). This is the one piece that's genuinely
  "restore from source," not "restore from backup," and it already works
  today.
- **`bm25_storage/` is not covered by `backup.sh` at all** (checked its
  file list directly — only `lightrag_storage/`, `graph_sampling_output/`,
  config files, and Qdrant are backed up). This isn't a gap worth fixing —
  it's cheap and fast to rebuild from the corpus with no LLM call, so
  "always rebuild" is the right answer for this one component specifically.

**Bottom line: nothing is currently restorable except MySQL (from the
committed SQL file) and `graph_sampling_output/` (already in git).**
Qdrant's vector data and `lightrag_storage/`'s graph/KV data must be
produced by a real, full `python ingest.py` run over the ~20k-product
catalog — this has never happened anywhere, local or otherwise. This is
the single biggest blocker, and it's independent of which cloud provider
gets chosen.

---

## 5. What in the current implementation would make deployment difficult?

Concrete points, not general cloud-deployment advice:

1. **No Dockerfile or docker-compose exists anywhere in either repo**
   (checked directly) — only the standalone `docker run` commands
   `README.md` documents for Qdrant/MySQL (the *dependencies*), nothing
   for the RA app itself. One would need to be written (new file, not a
   change to retrieval logic).
2. **`servers.txt`/`multi_ollama.py`'s round-robin load balancer assumes
   multiple local/LAN Ollama processes** — a topology built for someone's
   multi-GPU dev rig. It still works with one server listed, but it's a
   mismatch (over-built, not broken) for a single-cloud-instance
   deployment.
3. **Cold-start cost on first request**: `EMBED_BACKEND="local"`
   downloads `nomic-embed-text-v1.5` from HuggingFace at runtime
   (`local_embed.py`) — `ingest.py` already has an explicit `warmup()`
   step because of a real 60s timeout risk it caused before. `merge.py`'s
   CrossEncoder (`BAAI/bge-reranker-base` by default) is lazily loaded on
   first `fuse_and_rerank()` call and has the same cold-start shape.
   Neither is pre-warmed today.
4. **No timeout/deadline handling anywhere in `search_catalog()`** — a
   slow Qdrant/Ollama call currently just blocks. Already flagged as an
   open gap in `AGENTIC_INTEGRATION.md`; matters more once this serves
   real concurrent HTTP traffic instead of one CLI invocation at a time.
5. **`semantic_search.py` rebuilds the entire LightRAG object
   (`build_rag()`) on every single call** — reconnects to Qdrant,
   re-initializes storages — real avoidable latency under load that
   didn't matter for a one-shot CLI script.
6. **RA has never run as a long-lived process.** Every entrypoint today
   (`query.py`, `bm25_index.py`, `search_catalog.py`) is a one-shot CLI
   script (`if __name__ == "__main__":`) that starts, does one thing,
   exits. An always-on HTTP service is a small but real addition, not
   "deploy the existing code unchanged."
7. **Connection defaults are local-dev values** (`FLIPKART_DB_HOST`
   defaulting to `127.0.0.1`, credentials defaulting to
   `flipkart_user`/`flipkart_pass`) — fine as defaults, but a shared cloud
   deployment needs these set as real secrets (Droplet/App Platform env
   vars), not committed anywhere. This is deployment hygiene, not a code
   change.
8. **The full catalog has never been ingested end-to-end** (§4) —
   repeated here because it's the actual blocker, not a hosting detail.

None of these are architectural problems with RA's design — they're all
either "add a small new file" or "run a process that hasn't been run
before." Nothing here implies changing `merge.py`'s fusion logic,
`sql_filter.py`'s query shape, or any retrieval behavior.

---

## 6. Ollama — the one real open decision

Not resolved here, deliberately, since it changes cost/latency
significantly and isn't RA's call alone to make:

- **Option A**: provision a GPU Droplet for Ollama (DO's GPU tier is a
  separate, pricier product line than standard Droplets).
- **Option B**: keep Ollama on a local/team machine, reachable from the
  DO Droplet over the network (fragile — depends on that machine staying
  on and reachable, not a real "shared URL" solution).
- **Option C**: swap the LLM backend for the deployed instance specifically
  (only `ingest.py`'s `llm_model_func`/`embedding_func()` wiring would
  need to change per `IMPLEMENTATION.md`'s own stated migration path) —
  this genuinely is a code change, so it's flagged, not assumed.

This decision affects both the one-time full-catalog ingestion (§4) and
every live query afterward (LightRAG's `mode="mix"` query still calls the
LLM for keyword extraction at query time, not just at ingest time).

---

## 7. Deployment plan (DigitalOcean, minimal change)

**Phase 0 — Produce real data (blocks everything else, do this first,
locally)**
Run a full `python ingest.py` over the entire catalog against your
existing local Ollama setup (no cloud dependency for this step). This is
the one already-known-slow step (~1.5h+ for the extraction subset alone,
per `README.md`'s own estimate) — better to debug it locally where you
have direct visibility than on a fresh Droplet. Once it completes, run the
existing `./backup.sh` — it already knows how to package `lightrag_storage/`
and Qdrant's data correctly.

**Phase 1 — Provision the two data stores on DigitalOcean (lift-and-shift,
zero code changes)**
- MySQL: a Droplet running the same `docker run` MySQL setup
  `Vatsalya Version/README.md` documents (or DO Managed MySQL), load
  `flipkart_metadata.sql` fresh.
- Qdrant: a Droplet running the exact `docker run qdrant/qdrant --restart
  unless-stopped -v flipkart_qdrant_data:/qdrant/storage ...` command
  `RA/README.md` already documents, with a DO Volume backing the mount.
  Restore Phase 0's backup into it using the existing `./import.sh`
  (already handles recreating the named volume + container from a
  `qdrant_data.tar.gz`).

**Phase 2 — RA app Droplet**
Check out the repo, `uv sync` (or `pip install -r requirements.txt`),
restore `lightrag_storage/` from Phase 0's backup via `./import.sh` (or a
direct copy), let `bm25_storage/` rebuild itself automatically on first
run (cheap, no LLM). Set `FLIPKART_DB_HOST`/`QDRANT_URL`/etc. to point at
Phase 1's Droplets via real secrets, not committed defaults.

**Phase 3 — Resolve Ollama (§6)** before or alongside Phase 2, since
`semantic_search.py` needs it reachable at query time, not just for the
one-time ingest.

**Phase 4 — Add the thin HTTP wrapper (new file, not a modification)**
A small FastAPI/Flask app (e.g. `RA/api.py`) exposing one endpoint —
`POST /search` — that calls `search_catalog(query_state)` exactly as it
exists today and returns its JSON output. This is the only new
retrieval-adjacent code this plan requires, and it does not touch
`merge.py`, `search_catalog.py`, or anything they call.

**Phase 5 — Run it as a real service, not a script**
Run the HTTP wrapper under a process supervisor (systemd unit, or a Docker
container with `--restart unless-stopped`, matching the same
restart-policy discipline `README.md` already applies to Qdrant) so it
survives reboots/crashes — RA has never run as a persistent process
before (§5.6), so this is new operational surface, however small.

**Phase 6 — Validate**
Call the deployed URL from a machine that isn't the Droplet itself,
compare results for a few representative queries against a local run's
output for the same query — a regression check that the deployment
produced the same behavior, not just that it starts up.

---

## Open items for the team (not decided here)

1. Clarify with George: hosted retrieval **API** or hosted **database**? —
   they lead to different architectures (§2).
2. Decide Ollama's deployment story (§6) — cost/latency tradeoff, not a
   technical unknown.
3. Confirm the HTTP-wrapper approach (§2, §7 Phase 4) is acceptable before
   it gets written — it's new code, even though it's additive and doesn't
   touch existing retrieval logic.
