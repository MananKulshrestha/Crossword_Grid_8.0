# RA Server Setup — RunPod, No Docker

What was actually done to get MySQL + Qdrant + LightRAG working on this
RunPod pod, from a completely bare container to a working `./webui.sh`,
and what to run after a pod restart. `setup.sh` (in this folder) automates
everything below in one command — this doc explains *why* it does what it
does, so a restart or a fresh pod isn't a mystery.

## TL;DR

```bash
cd "Vatsalya Version/RA"
./setup.sh          # brings up MySQL + Qdrant + Tier 1 schema + LightRAG storage
./webui.sh           # starts the web UI on :8000 (first boot: ~90-120s warmup, see below)
```

Re-run `./setup.sh` any time — after a pod restart, or just to check
status. It detects what's already there and only does the missing work.

## 1. Why this isn't a `docker-compose up`

This repo's `README.md` documents a Docker-based MySQL/Qdrant setup. That
doesn't work in this environment:

```
docker: Cannot connect to the Docker daemon at unix:///var/run/docker.sock
dockerd: failed to mount overlay: operation not permitted
dockerd: exec: "iptables": executable file not found in $PATH
```

`dockerd` can't run here — no permission to mount `overlay` filesystems or
set up `iptables` NAT rules (standard restrictions in an unprivileged
sandbox/pod). This is a platform limit, not something to work around with
`--privileged` or similar. So MySQL and Qdrant run as **plain host
processes** instead of containers — everything downstream (`config.py`'s
`QDRANT_URL`, `db/build_tier1.py`'s `FLIPKART_DB_*` vars) only cares about
a reachable `host:port`, not how the process was started.

## 2. Why data lives under `/workspace/ra_data`, not the repo path

RunPod pods have two very different filesystems:

| Mount | Type | Survives a pod restart? |
|---|---|---|
| `/` (container root) | overlay, ~5GB | **No** — wiped on restart/recreation |
| `/workspace` | moosefs network mount (`mfs#euro.runpod.net`) | **Yes** — this is the persistent volume |

So anything installed the normal way (`apt-get install mysql-server`,
downloading the Qdrant binary to `/opt`) disappears on restart. All real
state — MySQL's datadir, Qdrant's binary + storage, the HuggingFace model
cache — was moved to `/workspace/ra_data/`, **not** left at its default
location, and **not** put directly under the repo path either (see next
section for why).

```
/workspace/ra_data/
├── mysql/data/       MySQL datadir (persistent)
├── qdrant/bin/qdrant  Qdrant binary (persistent, avoids a re-download every restart)
├── qdrant/storage/    Qdrant's data files (persistent)
├── hf_cache/           HuggingFace model cache (persistent, see §6)
└── logs/               qdrant.log, webui.log
```

`RA/runtime` is a symlink to `/workspace/ra_data/` so the data is still
browsable from inside the repo without actually living on the ephemeral
container filesystem. It's gitignored.

## 3. Why not `/workspace/Crossword_Grid_8.0/Vatsalya Version/RA/...` directly

Two real problems, both hit during setup:

- **Space in "Vatsalya Version".** Debian's `/etc/init.d/mysql` script
  does a `df` check on the datadir path that word-splits on spaces —
  pointing `datadir` inside `Vatsalya Version/` broke `service mysql
  start` outright (`df: /workspace/.../Vatsalya: No such file or
  directory`). `/workspace/ra_data` (no spaces) sidesteps this.
- **FUSE filesystem correctness.** Qdrant itself logs a warning at
  startup when its storage path is on a FUSE mount:
  `Filesystem check failed ... FUSE filesystems may cause data corruption
  due to caching issues`. This is a real, open tradeoff (see §8), not
  something resolved here — the data currently lives on `/workspace`
  anyway because that's the only thing that survives a restart, and the
  write pattern here (occasional restores, not constant heavy ingestion)
  is low-risk. Worth revisiting if `ingest.py` starts running regularly
  against this same Qdrant instance.

## 4. MySQL

```bash
apt-get install -y mysql-server
```

Config (`/etc/mysql/mysql.conf.d/mysqld.cnf`), patched idempotently by
`setup.sh`:
- `datadir = /workspace/ra_data/mysql/data`
- `bind-address = 0.0.0.0` (not the package default `127.0.0.1` — needed
  for any external port-forward to reach it, matches `../README.md`'s
  Docker setup reasoning)
- `port = 3307` (this repo's convention, not the MySQL default `3306`)

First-run only (persistent datadir empty):
```sql
CREATE DATABASE IF NOT EXISTS flipkart CHARACTER SET utf8mb4;
CREATE USER IF NOT EXISTS 'flipkart_user'@'%' IDENTIFIED WITH mysql_native_password BY 'flipkart_pass';
GRANT ALL PRIVILEGES ON flipkart.* TO 'flipkart_user'@'%';
ALTER USER 'root'@'localhost' IDENTIFIED WITH mysql_native_password BY 'rootpass';
FLUSH PRIVILEGES;
```

Then imports `../flipkart_metadata.sql` (the source dump, one level up
from `RA/`) with `INSERT IGNORE` (a couple of source rows share a
duplicate `sku_id`, see `../README.md` step 5):

```bash
sed 's/^INSERT INTO/INSERT IGNORE INTO/' ../flipkart_metadata.sql > /tmp/x.sql
mysql -u root -prootpass flipkart < /tmp/x.sql
```

Result: **19,998 unique SKUs** in `product_metadata`.

### Tier 1 normalized schema

The flat `product_metadata` table above is legacy shape. `db/build_tier1.py`
builds the real normalized schema (`products` / `skus` / `offers` /
`taxonomy_nodes` / `product_attributes` / `sku_attributes` / etc.) on top
of it and repoints `product_metadata` at a `VIEW` reconstructing the same
flat shape, so nothing reading `product_metadata` needs to change:

```bash
cd db
FLIPKART_DB_HOST=127.0.0.1 FLIPKART_DB_PORT=3307 \
FLIPKART_DB_USER=root FLIPKART_DB_PASSWORD=rootpass FLIPKART_DB_NAME=flipkart \
python build_tier1.py
```

Idempotent (safe to re-run — see the script's own docstring). `setup.sh`
gates this on the `skus` table not existing yet, so it only runs once per
fresh datadir.

**Result:**

| Table | Rows |
|---|---|
| `product_metadata` (view) | 19,998 |
| `skus` | 19,998 |
| `products` | 11,605 |
| `offers` | 19,998 |
| `taxonomy_nodes` | 9,308 |
| `product_attributes` | 5,975 |
| `sku_attributes` | 1,092 |

Verified the `skus` table's SKU set matches the legacy flat table's
exactly (0 diff) — the migration didn't drop or duplicate anything.

## 5. Qdrant

Ubuntu 20.04 here ships glibc 2.31 — Qdrant's standard prebuilt binary
needs 2.32+ and fails with `GLIBC_2.32' not found`. Used the **musl**
build instead (statically linked, no glibc dependency):

```bash
curl -sL https://github.com/qdrant/qdrant/releases/latest/download/qdrant-x86_64-unknown-linux-musl.tar.gz \
  -o /tmp/qdrant.tar.gz
tar xzf /tmp/qdrant.tar.gz -C /workspace/ra_data/qdrant/bin
QDRANT__STORAGE__STORAGE_PATH=/workspace/ra_data/qdrant/storage \
  nohup /workspace/ra_data/qdrant/bin/qdrant > /workspace/ra_data/logs/qdrant.log 2>&1 &
```

### It came up empty at first — and that was correct, not a bug

A fresh Qdrant start genuinely has zero collections. The catch: `RA/backups/`
*looked* like it only had a stray `.DS_Store` at first glance — turned out
the user had a real backup (`qdrant_data.tar.gz`, 236MB — a tarball of a
previous Qdrant `/qdrant/storage`, made by this repo's own `backup.sh`)
that just hadn't been copied into the repo checkout yet. Once it was:

```bash
tar xzf backups/qdrant_data.tar.gz -C /workspace/ra_data/qdrant/storage
```

Qdrant recovered all three collections cleanly on restart. **Verified
with a real point count, not just "collections exist":**

| Collection | Points |
|---|---|
| `lightrag_vdb_chunks_nomic_embed_text_768d` | 17,536 |
| `lightrag_vdb_entities_nomic_embed_text_768d` | 9,489 |
| `lightrag_vdb_relationships_nomic_embed_text_768d` | 14,380 |

Sanity-checked by scrolling real points back out — actual product text
(`"Suvarnadeep Rose Rhodium Zircon Sterling Silver Pendant..."`, etc.),
not placeholder/corrupt data.

## 6. LightRAG storage (the graph + doc status)

`RA/lightrag_storage/` (the KV stores, `.graphml` graph, doc status) isn't
committed to git and wasn't in the checkout either — restored from
`backups/lightrag_storage/` (also part of the same backup the user had):

```bash
cp -r backups/lightrag_storage RA/lightrag_storage
```

**Cross-checked SKU consistency across all three stores** (MySQL, Qdrant,
LightRAG doc_status) by pulling the actual SKU ID sets and diffing them,
not just comparing counts:

- LightRAG's `processed` docs and Qdrant's `chunks` collection SKU sets
  are **exactly equal** (17,534 = 17,534, zero difference either
  direction).
- Both are a clean subset of MySQL's 19,998 SKUs (zero missing) — nothing
  ingested that isn't a real product.
- The gap (19,998 − 17,534 = 2,464 MySQL SKUs never embedded) is
  explained by LightRAG's own content-level duplicate detection
  (`doc_status`'s `failed` entries carry `"Original doc_id: sku-..."`
  pointing at the SKU whose identical description was kept instead) —
  expected behavior, not data loss.

### HuggingFace model cache — also redirected to `/workspace`

The local embedding backend (`local_embed.py`, `nomic-ai/nomic-embed-text-v1.5`
via `sentence-transformers`) downloads its weights from HuggingFace on
first use, into `~/.cache/huggingface` by default — which is on the
ephemeral root filesystem. Without redirecting this, every pod restart
would re-download a ~1GB model. Added to `.env`:

```bash
HF_HOME=/workspace/ra_data/hf_cache
```

`config.py` already calls `load_dotenv(override=False)`, so this is picked
up automatically by anything that imports `config` before touching
`local_embed`.

## 7. Two real bugs found (and fixed) while testing the actual query pipeline

Getting data into MySQL/Qdrant wasn't the end of it — `./webui.sh` was
reported as "stuck on searching". Root-caused both issues from
`lightrag.log`, not guessed:

### 7a. `web_ui.py`: every query after the first silently failed

`lightrag.log` showed `ERROR - Query failed: Event loop is closed` on the
second and third queries. Cause: `web_ui.py`'s `run_async_in_thread()`
created a **new** asyncio event loop per Flask request and closed it
afterward, but the cached global `rag` instance's internal LLM/embedding
worker pools are bound to whichever loop first built them. Query #1
built those pools and worked; every query after that tried to use pools
bound to an already-closed loop. LightRAG caught the error internally and
returned `None`, so Flask still reported `HTTP 200` with an empty
`context` — it looked like a silent hang, not a crash.

**Fix**: one persistent background event loop, alive for the app's
lifetime, with every request submitted to it via
`asyncio.run_coroutine_threadsafe()` instead of `new_event_loop()` +
`close()` per request. See `run_async_in_thread()` in `web_ui.py`.

### 7b. Cold embedding-model load races LightRAG's internal 60s timeout

Even with 7a fixed, a **fresh process's first** query still stalled for
~90-120 seconds. `ingest.py` already has a documented fix for exactly this
(`local_embed.warmup()`, forces the `sentence-transformers` model to load
synchronously *before* any real embedding call, so the cold load never
races LightRAG's internal 60s per-worker timeout) — but `query.py` (and
therefore `web_ui.py`, which reuses `query.py`'s `build_query_rag()`)
never called it.

**Fix**: added the same `warmup()` call to `query.py`'s
`build_query_rag()`. Additionally made `web_ui.py` call it **at server
startup**, before `app.run()`, rather than lazily on the first API
request — so the ~90-120s cost is paid once while the server is still
"starting up" (visible in the terminal as `"Warming up query engine..."`),
not by whichever user sends the first real query.

### 7c. `run.sh query` was blocked by an Ollama check it doesn't need

`run.sh`'s Ollama-extraction-server reachability check ran unconditionally,
even for `./run.sh query "..."` — which never touches Ollama by default
(`INFERENCE_BACKEND=deepinfra`, `EMBED_BACKEND=local`). Fixed by gating
that whole check block on `[ "$ACTION" != "query" ]`.

## 8. Where the ~90-120s actually goes (measured, not guessed)

Diagnosed this precisely rather than assuming "the model is slow":

| Step | Time | Verdict |
|---|---|---|
| DeepInfra API call (keyword extraction / LLM) | 6.6s | Fine |
| Local embedding model load (from HF cache) | 8.2s | Fine |
| Local embedding `encode()` call | 0.16s cold / 0.04s warm | Fine |
| **`import torch`** | ~17s | Slow |
| **`import sentence_transformers`** | **~75-95s** | **The actual bottleneck** |

Confirmed this is chronic, not one-time bytecode compilation, by
re-running the import twice (79.2s both times). Root cause: `.venv` lives
on the same FUSE-mounted `/workspace`, and importing a large package tree
(`torch`/`transformers`/`sentence_transformers`, hundreds of small files)
over a network filesystem is inherently slow — every file open is a round
trip. **Not fixed** — a full `.venv` copy to local disk was attempted to
verify/fix this properly, but aborted partway through (`.venv` is 8.5GB;
the ephemeral root disk is only 5GB total, and the copy nearly filled it).
If this becomes worth fixing properly: rebuild `.venv` on local disk
(`/opt` or similar) via `uv sync` after each pod boot — `uv`'s package
cache makes that fast — and keep only the actual *data* (MySQL, Qdrant,
HF model weights) on `/workspace`.

## 9. Verifying everything works end-to-end

```bash
# Web UI (after ./webui.sh, wait for "Query engine ready." in its log)
curl -s -X POST http://localhost:8000/api/query \
  -H 'Content-Type: application/json' \
  -d '{"query":"What waterproof footwear brands are available?"}'

# CLI, one-shot (pays the cold-start cost fresh each time -- see §8)
source .venv/bin/activate
./run.sh query "What waterproof footwear brands are available?"
```

Both were run for real and returned genuine grounded context — actual
product chunks (Clarks Dunbar Racer Boat Shoes, FLITE Slippers "WATER
PROOF ANTI SKID", ...), entities, and relationships, not empty/error
responses.

## 10. After a pod restart

```bash
cd "Vatsalya Version/RA"
./setup.sh      # ~10-20s if data already exists; longer only on a truly fresh pod
./webui.sh       # first request after this needs ~90-120s startup warmup (§6/§8), then fast
```

`setup.sh` is safe to run repeatedly — it only (re)installs/(re)seeds
whatever's actually missing, never re-imports or re-seeds data that's
already sitting in `/workspace/ra_data`.

## 11. Open items / not done

- **Qdrant-on-FUSE risk (§3)** — not resolved, just currently low-risk
  since nothing's writing heavily to it. Revisit if regular `ingest.py`
  runs start happening against this instance.
- **`.venv` import slowness (§8)** — not fixed, root cause identified.
  Local-disk `.venv` + `uv sync` on boot is the likely fix, not yet done.
- `README.md`'s "No-Docker fallback" section (committed separately, same
  session) claims `flipkart_lightrag_corpus.md` is missing SKU
  `GRPEFKPR8Y9W66AZ`. Checked directly while writing this doc: that SKU
  **is** present in the corpus file (20,000 `Product ID:` blocks total,
  not 19,999). That README claim looks stale/inaccurate — worth fixing
  there, not treated as fact here.
