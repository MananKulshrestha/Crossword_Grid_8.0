# Documentation Index — What to Read When

This is your **navigation guide** for all RA module documentation. Pick your scenario below.

---

## 🚀 I Just Want to Use It (5 minutes)

**Read**: [`QUICK_START.md`](QUICK_START.md)

Contains:
- 7-step setup (copy-paste commands)
- How to run queries (CLI, web UI, Python code)
- Troubleshooting for common issues

**Then**: Try the examples, ask questions if needed.

---

## 🔧 I'm Integrating LightRAG into an Agent (30 minutes)

**Start here**: [`AGENTIC_PIPELINE_SETUP.md`](AGENTIC_PIPELINE_SETUP.md)

Contains:
- Pre-flight checklist (what needs to exist)
- How agents call LightRAG (4 integration patterns)
- Code examples: minimal, production-scale, framework-specific
- Configuration cheat sheet
- Troubleshooting by symptom

**Why this first**: This doc is written for developers building agentic tools, not end users.

**Then**: Jump to [`INTEGRATION_VERIFICATION.md`](#) (section 7) for production deployment checklist.

---

## ✅ Is Everything Actually Complete?

**Read**: [`INTEGRATION_VERIFICATION.md`](INTEGRATION_VERIFICATION.md)

Contains:
- DeepInfra integration status (fully complete ✓)
- Checklist of all required code/functions (all present ✓)
- What works, what's missing, what's intentional
- Reproduction steps to verify everything works
- Deployment checklist before production

**Why**: Confirms no pieces are missing, nothing is broken.

---

## 📚 I Need Deep Context — Design & Architecture

### Why things are built the way they are
**Read**: [`IMPLEMENTATION.md`](IMPLEMENTATION.md)

Contains:
- Design decisions for this folder
- Why SQL/BM25/LightRAG are separate branches
- Entity/relationship schema design
- Checkpoint/resume mechanics

---

### How the knowledge graph is built
**Read**: [`lightrag-implementation.md`](lightrag-implementation.md)

Contains:
- Why only 2,673 of 20,000 SKUs get full graph extraction
- How coverage-based sampling works
- The entity-extraction prompt (product-specific ontology)
- Relationship keywords (fixed vocabulary)

---

### The bigger picture: retrieval architecture
**Read**: [`retrieval-architecture.md`](retrieval-architecture.md)

Contains:
- All three branches and how they should merge
- SQL intersection logic (mandatory, never skip)
- BM25 ∪ semantic (union, not intersection)
- Cross-encoder reranking
- Data flow from query to results

---

### How to use LightRAG programmatically
**Read**: [`integration.md`](integration.md)

Contains:
- What data went where (SQL, BM25, LightRAG)
- How to call each function (`eligible_skus()`, `search()`, `aquery()`)
- Backup/restore workflow
- Diagnostic helpers
- What's NOT built yet (for future agents)

---

## 🔄 I'm Restoring from Backup or Resuming

**Read**: [`README.md`](README.md) — "Qdrant Setup" & "LightRAG Import" sections

Or directly:

**Setup Qdrant (first time)**: [`README.md`](README.md#qdrant-setup-docker--vector-database)

**Restore from backup**: [`README.md`](README.md#lightrag-import--restore-from-backup)

**Manual restore** (no `import.sh`): [`integration.md`](integration.md#manual-setup-without-importsh-equivalent-steps-if-you-need-control) section 2

---

## 🗄️ I'm Running Ingestion (Adding Products to Index)

**Read**: [`README.md`](README.md#qdrant-setup-docker--vector-database) section 0 (server setup), then section 3 (how to ingest)

Or directly: [`RA/README.md`](README.md) (this folder's README) sections 1-4

Contains:
- How to start Ollama servers
- How to run `python ingest.py`
- Checkpoint/resume behavior (hard interrupts are safe)
- Time estimates for full catalog

**Then**: Use `python check_ingestion_status.py` to verify data landed in both LightRAG storage and Qdrant.

---

## 💻 I'm Setting Up from Scratch (MySQL + Qdrant + Ingestion)

**Follow in order**:

1. **Parent README**: [`../README.md`](../README.md) — MySQL setup (Docker)
2. **This folder's README**: [`README.md`](README.md) — Qdrant setup (Docker) + ingestion steps
3. **Python setup**: [`QUICK_START.md`](QUICK_START.md#setup-one-time--2-minutes) — Install packages
4. **Ingest**: [`README.md`](README.md) section 4 — Run `python ingest.py`
5. **Verify**: [`INTEGRATION_VERIFICATION.md`](INTEGRATION_VERIFICATION.md#7-reproduction-checklist--run-this-to-verify-everything-works) — Run diagnostic commands

---

## 🐛 Something's Broken or Stuck

**Read**: [`AGENTIC_PIPELINE_SETUP.md`](AGENTIC_PIPELINE_SETUP.md#6-troubleshooting)

Or directly: [`README.md`](README.md) — "Troubleshooting" section in both main and RA READMEs.

**Systematic debugging**:
```bash
python check_storage.py              # Local storage stats
python check_ingestion_status.py     # LightRAG storage vs. Qdrant sync check
python diagnose.py                   # Connectivity check + error logs
```

Each tool prints clear diagnostics. Run them in order.

---

## 📖 Complete Documentation Map

| File | Purpose | Audience | Read Time |
|------|---------|----------|-----------|
| **`QUICK_START.md`** | Get working in 5 min | Everyone | 5 min |
| **`AGENTIC_PIPELINE_SETUP.md`** | Integrate into agents | Developers | 30 min |
| **`INTEGRATION_VERIFICATION.md`** | Verify completeness | Architects | 15 min |
| **`integration.md`** | API & function reference | Developers | 20 min |
| **`IMPLEMENTATION.md`** | Design decisions (SQL/BM25/LightRAG) | Architects | 20 min |
| **`lightrag-implementation.md`** | Graph subset design & entity prompt | Researchers | 25 min |
| **`retrieval-architecture.md`** | Complete retrieval pipeline design | Architects | 30 min |
| **`README.md`** | Setup steps (Qdrant, ingestion, queries) | DevOps/Developers | 20 min |
| **`../README.md`** | MySQL setup (Docker) | DevOps | 10 min |
| (Diagnostic tools) | `check_storage.py`, `check_ingestion_status.py`, `diagnose.py` | Debugging | 5 min each |

---

## 🎯 Navigation by Role

### **End User (Just want to query)**
1. `QUICK_START.md` — Setup & query commands
2. `README.md` section 0-2 — Database/Qdrant setup if needed

### **Frontend Developer (Building UI)**
1. `QUICK_START.md` — Setup
2. `README.md` section 5 — Query interface
3. (Code reference) Look at `web_ui.py` / `query.py` for patterns

### **Backend/LLM Engineer (Building agents)**
1. `AGENTIC_PIPELINE_SETUP.md` — Integration patterns (sections 3.1-3.3)
2. `integration.md` section 3 — API reference
3. `QUICK_START.md` — Fallback examples

### **Architect (Designing larger system)**
1. `retrieval-architecture.md` — Full design
2. `IMPLEMENTATION.md` — Design decisions
3. `lightrag-implementation.md` — Graph subset specifics
4. `INTEGRATION_VERIFICATION.md` — What's built vs. what's missing

### **DevOps (Deploying/managing infra)**
1. `README.md` — Docker setup (Qdrant, MySQL)
2. `QUICK_START.md` — Configuration
3. `AGENTIC_PIPELINE_SETUP.md` section 7 — Production checklist

### **Researcher (Understanding methodology)**
1. `lightrag-implementation.md` — Entity extraction, coverage-based sampling
2. `retrieval-architecture.md` — Fusion strategy
3. `IMPLEMENTATION.md` — Design choices and tradeoffs

---

## 🔗 Key Entry Points by Use Case

### "I want to query products"
→ `QUICK_START.md` → `README.md`

### "I'm building an agent that uses LightRAG"
→ `AGENTIC_PIPELINE_SETUP.md` → `integration.md`

### "I'm deploying this to production"
→ `INTEGRATION_VERIFICATION.md` section 7 → `README.md`

### "I'm restoring from a backup"
→ `README.md` "LightRAG Import" section → `integration.md` section 2

### "I need to ingest new products"
→ `README.md` section 4 → `lightrag-implementation.md` (if graph needs updating)

### "Something's broken"
→ `AGENTIC_PIPELINE_SETUP.md` section 6 → Diagnostic scripts → Open issue

### "I want to understand the architecture"
→ `retrieval-architecture.md` → `IMPLEMENTATION.md` → `lightrag-implementation.md`

---

## 📝 File Organization

```
RA/
├── config.py                          # Configuration (all env vars documented inline)
├── .env.example                       # Template for .env (copy and fill in)
├── requirements.txt                   # Python dependencies
│
├── QUICK_START.md                     # ← Start here (5 min)
├── AGENTIC_PIPELINE_SETUP.md          # Integration guide (30 min)
├── INTEGRATION_VERIFICATION.md        # Completeness checklist
│
├── DOCS_INDEX.md                      # This file (navigation guide)
├── README.md                          # Complete setup & usage
├── integration.md                     # API reference
├── IMPLEMENTATION.md                  # Design decisions
├── lightrag-implementation.md         # Graph subset design
├── retrieval-architecture.md          # Big-picture architecture
│
├── ingest.py                          # Ingestion pipeline
├── query.py                           # Query entrypoint (CLI)
├── web_ui.py                          # Flask web UI
├── sql_filter.py                      # SQL hard-filtering branch
├── bm25_index.py                      # BM25 search branch
├── load_documents.py                  # Corpus parser (shared)
├── multi_ollama.py                    # Ollama load-balancer
├── deepinfra_llm.py                   # DeepInfra query backend
├── graph_sampling.py                  # Subset selection
│
├── check_storage.py                   # Diagnostic: storage stats
├── check_ingestion_status.py          # Diagnostic: storage vs. Qdrant sync
├── diagnose.py                        # Diagnostic: connectivity
│
├── backup.sh                          # Create checkpoints
├── import.sh                          # Restore from backup
├── run.sh                             # Ingest + query runner
├── webui.sh                           # Start web UI
├── reset.sh                           # Full wipe (for testing)
│
├── graph_sampling_output/             # Pre-computed subset (2,673 SKUs)
│   ├── full_extraction_skus.txt       # Which SKUs to extract
│   └── coverage_report.json           # Coverage statistics
│
├── lightrag_storage/                  # Persistent graph/KV/cache (created by ingest.py)
├── bm25_storage/                      # BM25 index (created by bm25_index.py)
└── backups/                           # Backup ZIPs (created by backup.sh)
```

---

## 💡 Pro Tips

1. **Skim before deep-diving**: Read the intro of each file to understand scope before reading fully.
2. **Start with examples**: Most docs have copy-paste examples. Try those first, understand after.
3. **Diagnostic scripts are your friend**: Stuck? Run `check_ingestion_status.py` and `diagnose.py`.
4. **Configuration is your escape hatch**: Before modifying code, check if `config.py` has a setting for it.
5. **Ingestion is checkpoint-safe**: Hard interrupt? Just re-run `python ingest.py`. Nothing is lost.

---

## ❓ Still Confused?

Check which section **matches your current problem**:

- **"I don't know where to start"** → Read `QUICK_START.md` (5 min)
- **"It's not working"** → Run diagnostic tools (5 min)
- **"I need to understand how this works"** → Read `retrieval-architecture.md` (30 min)
- **"I want to integrate this into my system"** → Read `AGENTIC_PIPELINE_SETUP.md` (30 min)
- **"Is everything actually complete?"** → Read `INTEGRATION_VERIFICATION.md` (15 min)

That's it. Everything else is reference material.

---

**Last updated**: August 2026  
**Status**: All code complete. DeepInfra integration verified. Ready for production.

