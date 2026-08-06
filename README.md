# Crossword Grid: Contract-First Agentic Chat Orchestration

A production-grade multi-agent orchestration system for conversational shopping assistance with strict contract semantics, hard error boundaries, and deterministic tool composition.

## Overview

This system implements a turn-based orchestrator that processes user queries through a pipeline:

```
User Query → Intent Extraction → Route → Tool Execution → Response → Memory
```

Every stage validates against explicit contracts, records its work on a trace, and halts on any error—no retries, no silent fallbacks, no stale data returned in place of answers.

**Stack:** FastAPI (backend) • React + TypeScript (frontend) • Pydantic (contracts) • BM25 + MySQL (search + truth)

---

## Quick Start

### Prerequisites
- Python 3.12+
- Node.js 18+
- UV package manager (`pip install uv`)
- MySQL (for catalog & cart data)

### Backend Setup

```bash
# Install dependencies
uv sync --all-groups

# Environment configuration
export ANTHROPIC_API_KEY="your-key-here"
export RERANKER_ENDPOINT="http://localhost:8000/rerank"  # or your reranker service
export MYSQL_HOST="localhost"
export MYSQL_USER="root"
export MYSQL_PASSWORD="password"
export MYSQL_DB="fkgrid"

# Run tests
uv run pytest

# Start server
uv run uvicorn src.fkgrid.api:app --reload
```

### Frontend Setup

```bash
cd frontend

# Install dependencies
npm install

# Development
npm run dev        # http://localhost:5173

# Production build
npm run build

# Type checking
npm run lint
```

---

## Architecture

### Backend Pipeline

#### 1. **Orchestrator** (`src/fkgrid/orchestrator.py`)

The central coordinator processing each turn:

```
Extract Query → Enhance → Route to Agents → Resolve References → Execute Actions → Record Memory
```

**Key principle:** Every stage produces a `TraceStep` (input, output, success flag). On any error (LLM timeout, MySQL failure, invalid reference), return an explicit `ERROR` TurnResult immediately—no recovery, no workarounds.

```python
TurnResult(
  status: "OK" | "ERROR",
  message: str,
  error_code: str | None,
  trace: list[TraceStep],  # Full audit log
)
```

#### 2. **Query Extraction** (`src/fkgrid/llm.py`)

Single LLM call to extract structured intent from user message.

**System Prompt Philosophy:**
- Defines action types (CHITCHAT, SEARCH, REFINE, COMPARE, UPDATE_CART, etc.)
- Specifies exact JSON schema the model must follow
- Lists reranker's accepted constraints (hard_constraints only, no brand/min_price)
- Includes 0-based rule examples for reference handling

**Input:** `ChatTurn` (role, content, timestamp) + recent chat history

**Output:** `QueryExtraction` Pydantic model (validated before use)

```python
class QueryExtraction(BaseModel):
  action: Action  # enum
  query_terms: list[str]
  constraints: list[Constraint]  # Only ["max_price", "category", "size", "stock_status"]
  references: list[Reference]  # Ordinal (1-based), SKU ID, or "all"/"count"
  cart_operations: list[CartOperationDraft]
  reply: str | None  # For CHITCHAT
```

#### 3. **Query Enhancement** (`src/fkgrid/enhancer.py`)

**Not** an LLM call—deterministic transformation. Copies extracted values into reranker request shape:

- Merges refinement query_terms into soft_query_text
- Lifts constraints into hard_constraints
- Clears any fields the shopper explicitly dropped
- Never invents data the model didn't extract

#### 4. **Catalog & Search** (`src/fkgrid/catalog.py`)

Two-layer system:

**Layer 1: Reranker Endpoint**
- Performs retrieval, RAG, semantic ranking
- Accepts `RerankerRequest` (soft_query, hard_constraints, limit)
- Returns ranked list of SKU IDs

**Layer 2: MySQL Verification**
- Every SKU returned by reranker is verified against MySQL
- Pulls product details (title, price, availability, stock)
- Preserves reranker's rank order
- Raises `CatalogError` on any MySQL failure—never returns stale cached data

```python
SearchResult(
  entries: list[SearchEntry],  # SKU, title, price, category, etc.
  total: int,
)
```

#### 5. **Reference Resolution** (`src/fkgrid/orchestrator.py`)

Maps user references to actual products:

| Reference | Resolves to |
|-----------|-------------|
| `ordinal=2` | Second entry in last search results |
| `sku_id="ABC123"` | Exact SKU (verified against MySQL) |
| `all=True` | All entries from last search |
| `count=3` | First 3 entries from last search |

All resolved SKU IDs are checked against MySQL. Hallucinated SKUs are flagged in the trace as `found: False`.

#### 6. **Cart Management** (`src/fkgrid/cart.py`)

Supports four operations:

```python
class CartOperationType(str, Enum):
  ADD_ITEM = "ADD_ITEM"         # Reference to catalog entry
  SET_QUANTITY = "SET_QUANTITY" # Change quantity for item_id
  REMOVE_ITEM = "REMOVE_ITEM"   # Remove item_id
  CLEAR_CART = "CLEAR_CART"     # Empty cart
```

Each operation requires explicit confirmation flag. Changes are committed to MySQL immediately—no local staging.

#### 7. **Response Generation & Memory**

- `ResponseAgent` composes final text response using conversation context
- `Memory` stores turn history keyed by session ID
- `Followups` tracks which products were shown to enable reference resolution in next turns

### Frontend Architecture

```
Browser
├─ Chat UI (React)
│  └─ Message List + Input
├─ API Client (Axios + WebSocket)
│  ├─ REST calls: POST /chat, GET /cart
│  └─ WebSocket: /ws/chat (token streaming)
└─ State Management
   ├─ Chat history
   ├─ Search results
   ├─ Cart contents
   └─ UI state (loading, errors)
```

**Key Flow:**
1. User types query, hits send
2. Frontend sends `TurnRequest` to `/chat`
3. Backend returns immediately with `turn_id`
4. Frontend opens WebSocket to `/ws/chat/{turn_id}` for response streaming
5. Tokens arrive as text chunks, rendered in real-time

---

## Prompt Engineering Guide

### System Prompt Structure

The extraction prompt has three sections:

#### 1. **Schema Definition**
Define the exact JSON shape the model must output. Include inline comments:
```json
{
  "action": "SEARCH" | "REFINE" | "CHITCHAT" | "...",
  "query_terms": [string],
  "constraints": [{"field": "max_price"|"category"|..., "value": any}],
  ...
}
```

**Why inline:** Model sees the valid field names and value types in one place.

#### 2. **Rules Section**
State the action semantics and constraint domain:

```
- SEARCH: fresh product request
- REFINE: narrowing CURRENT search (cheaper, a color, a size, dropping a filter)
- query_terms for REFINE are ADDED words, not replacement
- constraints: only these fields exist in reranker (max_price, category, size, stock_status)
- No brand field and no minimum-price field (brands in query_terms, price floor dropped silently)
```

**Why explicit:** Prevents the model from inventing fields the reranker will reject.

#### 3. **Examples Section**
Show concrete before/after transformations:

```
1. ("running shoes") "only adidas" 
   → REFINE, query_terms=["adidas"] 
   (caller merges to "running shoes adidas")

2. ("clothes", hard_constraints has size=M) "any size is fine" 
   → REFINE, query_terms=[], clear_constraints=["size"]

3. "add the first 3 to my cart" 
   → UPDATE_CART, cart_operations=[{"type": "ADD_ITEM", "reference": {"count": 3}}]
```

**Why examples:** Model learns from your specific domain (e.g., how to handle "the first N", how to interpret refinement).

### Testing Prompts

Every prompt change should be validated against test cases:

```python
# tests/test_extraction.py
@pytest.mark.parametrize("user_message,expected_action", [
    ("find running shoes", Action.SEARCH),
    ("only under ₹5000", Action.REFINE),
    ("add the first 3 to cart", Action.UPDATE_CART),
    ("thanks!", Action.CHITCHAT),
])
def test_extraction(user_message, expected_action):
    result = extract_query(user_message, [])
    assert result.action == expected_action
```

### Avoiding Common Pitfalls

1. **Hallucinated Fields**
   - ❌ Prompt allows "brand" in constraints → reranker 400s
   - ✅ Prompt states "brands go in query_terms" → model obeys

2. **Ambiguous References**
   - ❌ "compare these" with no ordinals → reference is None
   - ✅ Prompt example: "compare the second and third" → ordinals=[2, 3]

3. **Query Merging Confusion**
   - ❌ Model rewrites entire query on REFINE → loses context
   - ✅ Prompt rule: "query_terms are ADDED words, caller appends them"

4. **Stale Constraints**
   - ❌ "any size" returns action=REFINE but doesn't clear size constraint
   - ✅ Prompt rule: explicit clear_constraints=[...] field for removed filters

---

## Tool Design

### Tool Contracts

Each tool is a pure function with explicit input/output contracts:

```python
def search_catalog(request: RerankerRequest) -> SearchResult:
  """
  Input: soft_query, hard_constraints, limit
  Output: SearchResult with verified SKUs
  Raises: RerankerError (endpoint failure), CatalogError (MySQL failure)
  """
```

**Principle:** No optional returns, no None-wrapped results, no silent fallbacks. Either return valid data or raise a named error.

### Tool Composition

Tools are composed inside the Orchestrator, not in the LLM prompt:

```python
# ✅ Correct: Orchestrator calls tools, passes results to LLM if needed
extraction = extract_query(message, history)  # LLM
results = search_catalog(request)              # Tool
references = resolve_references(extraction.references, results)  # Deterministic

# ❌ Wrong: LLM tries to call tools directly
# (No function_calling—the LLM extracts intent, orchestrator invokes tools)
```

### Preventing Tool Hallucination

The system uses **deterministic schemas**, not free-form text:

```python
# LLM output: structured QueryExtraction
class QueryExtraction(BaseModel):
  action: Action
  query_terms: list[str]
  references: list[Reference]  # Explicit Reference shape

# Not: "call search_products('shoes')" (tool invocation as prose)
```

**Why:** Pydantic validates before the tool runs. Invalid output raises `ValidationError`, which the orchestrator catches and returns as ERROR TurnResult.

---

## Dependency Management

### Python Dependencies (UV)

**Dependency File:** `pyproject.toml`

```toml
[project]
requires-python = ">=3.12"
dependencies = [
  "fastapi>=0.115,<1",          # Web framework
  "pydantic>=2.7,<3",           # Schema validation
  "PyMySQL>=1.1,<2",            # Database driver
  "rank-bm25>=0.2.2",           # BM25 scoring (local fallback)
  "uvicorn>=0.30,<1",           # ASGI server
]

[project.optional-dependencies]
provider = ["httpx>=0.27,<1"]   # For LLM provider calls
dev = [
  "httpx>=0.27,<1",
  "pytest>=8,<9",
  "ruff>=0.6,<1",               # Linter
  "mypy>=1.11,<2",              # Type checker
  "tqdm>=4.70.0",
  "requests>=2.34.2",
]
```

**Installing:**
```bash
# All groups (dev, test, etc.)
uv sync --all-groups

# Production only
uv sync
```

**Versioning Strategy:**
- Use semver bounds (`>=X.Y,<X+1`) to catch breaking changes
- Pin to minor version for CLI tools (ruff, pytest) to avoid formatting/output drift
- Never `*` pin unless the package promises strict backward-compat

### Node Dependencies (NPM)

**File:** `frontend/package.json`

```json
{
  "dependencies": {
    "react": "^18.3.1",
    "axios": "^1.7.9",
    "react-router-dom": "7.18.2",
    "tailwindcss": "^3.4.17"
  },
  "devDependencies": {
    "typescript": "^5.7.2",
    "vite": "^6.0.5",
    "eslint": "^9.17.0"
  }
}
```

**Lock Strategy:**
- `^1.2.3` allows minor updates (1.2.3 to 1.9.9) → safe for libraries
- `7.18.2` pins exact version → react-router-dom had breaking changes in v8, so pin to v7
- Use `npm ci` in CI (respects lock file exactly)

### Managing Environment Variables

**File:** `.env.local` (not committed)

```bash
# Backend
ANTHROPIC_API_KEY="sk-..."
RERANKER_ENDPOINT="http://reranker:8000/rerank"
MYSQL_HOST="localhost"
MYSQL_USER="root"
MYSQL_PASSWORD="root"
MYSQL_DB="fkgrid"

# Frontend
VITE_API_URL="http://localhost:8000"
VITE_WS_URL="ws://localhost:8000"
```

**Loading:**
- Backend: `config.py` reads via `os.getenv()`
- Frontend: Vite auto-loads `VITE_*` prefixed vars into `import.meta.env`

### Updating Dependencies Safely

**Python:**
```bash
# Check for outdated packages
uv pip list --outdated

# Update specific package
uv sync --upgrade-package pydantic

# Update all (respects semver bounds in pyproject.toml)
uv sync --upgrade
```

**Node:**
```bash
# Check outdated
npm outdated

# Update specific
npm install axios@latest

# Safe update (respects ^ bounds)
npm update
```

### Dependency Security

- **Python:** Use `pip-audit` (in dev group) to scan for vulnerabilities
  ```bash
  uv run pip-audit
  ```
- **Node:** `npm audit` built-in
  ```bash
  npm audit
  npm audit fix  # Auto-patch
  ```

---

## Testing

### Test Structure

```
tests/
├── test_extraction.py          # Query extraction (LLM)
├── test_tool_integration.py    # Tool composition
├── test_api.py                 # API endpoints
├── test_agentic_workflow.py    # Full turn pipeline
└── manual_shopper_chat.py      # CLI for manual testing
```

### Running Tests

```bash
# All tests
uv run pytest

# Specific file
uv run pytest tests/test_extraction.py -v

# With coverage
uv run pytest --cov=src.fkgrid tests/
```

### Example: Testing a Tool

```python
# tests/test_tool_integration.py
def test_catalog_search():
    request = RerankerRequest(
        soft_query="running shoes",
        hard_constraints=[],
        limit=10,
    )
    result = search_catalog(request)
    assert len(result.entries) > 0
    # Verify every SKU is in MySQL
    for entry in result.entries:
        details = catalog.get_details(entry.sku_id)
        assert details.found

def test_reference_resolution():
    results = [
        SearchEntry(sku_id="SKU1", title="Shoe A", price=1000),
        SearchEntry(sku_id="SKU2", title="Shoe B", price=2000),
    ]
    
    # Ordinal reference
    ref = Reference(ordinal=2)
    entry = resolve_entry(ref, results)
    assert entry.sku_id == "SKU2"
    
    # "Count" reference
    ref = Reference(count=1)
    entry = resolve_entry(ref, results)
    assert entry.sku_id == "SKU1"
```

---

## API Contract

### POST /chat

**Request:**
```json
{
  "session_id": "uuid",
  "user_message": "find shoes under 5000",
  "use_session_history": true
}
```

**Response (immediate):**
```json
{
  "turn_id": "uuid",
  "status": "PROCESSING"
}
```

**Response (if error):**
```json
{
  "status": "ERROR",
  "message": "MySQL connection failed",
  "error_code": "DB_ERROR",
  "trace": [...]
}
```

**WebSocket: /ws/chat/{turn_id}**

Streams response tokens:
```
data: {"text": "I found ", "done": false}
data: {"text": "some great ", "done": false}
data: {"text": "shoes.", "done": true}
```

### GET /cart/{session_id}

**Response:**
```json
{
  "items": [
    {
      "sku_id": "SKU1",
      "title": "Running Shoes",
      "quantity": 2,
      "price": 1500
    }
  ],
  "total": 3000
}
```

---

## Error Handling

**Philosophy:** Fail hard, trace deeply.

Every error returns:
```python
TurnResult(
  status=TurnStatus.ERROR,
  message="Reranker endpoint unreachable",
  error_code="RERANKER_TIMEOUT",
  trace=[
    TraceStep(stage="extract", input={...}, output={...}, ok=True),
    TraceStep(stage="enhance", input={...}, output={...}, ok=True),
    TraceStep(stage="search", input={...}, output={...}, ok=False),  # ← failure point
  ]
)
```

**No Recovery:**
- LLM timeout → ERROR (don't retry)
- MySQL failure → ERROR (don't use cached data)
- Invalid reference → ERROR (don't skip to next operation)
- Reranker 400 → ERROR (don't fall back to BM25)

The trace shows exactly where the turn failed, making debugging straightforward.

---

## Development Workflow

### Modifying a Prompt

1. Update `_EXTRACTION_SYSTEM_PROMPT` in `src/fkgrid/llm.py`
2. Add test cases to `tests/test_extraction.py`
3. Run tests locally:
   ```bash
   uv run pytest tests/test_extraction.py -v
   ```
4. If tests pass, test end-to-end with manual CLI:
   ```bash
   uv run python src/fkgrid/cli.py "find shoes under 5000"
   ```
5. Commit with message: `Update extraction prompt: [change summary]`

### Adding a New Tool

1. Define contract in `src/fkgrid/contracts.py` (input/output Pydantic models)
2. Implement tool in dedicated module (e.g., `src/fkgrid/my_tool.py`)
3. Add to Orchestrator in `src/fkgrid/orchestrator.py` (only call site)
4. Write tests in `tests/test_tool_integration.py`
5. Update extraction prompt if the tool opens new actions

### Debugging a Failed Turn

1. Check the `trace` field in the TurnResult—it shows every step
2. Look for the stage with `ok=False`
3. Inspect that step's `input` and `output` to see what went wrong
4. Run the same inputs in isolation to reproduce

Example:
```python
# Turn failed at "search" stage
# trace[2] = TraceStep(stage="search", input={"query": "shoes"}, output={}, ok=False)

# Reproduce:
from src.fkgrid import catalog
result = catalog.search({"query": "shoes"})  # Will raise CatalogError
```

---

## Performance Tuning

### Caching

- LLM config is cached with `@lru_cache(maxsize=1)` (loaded once per process)
- Reranker config similarly cached
- MySQL connections not cached—each query opens a connection (keep-alive in production)

### Concurrency

- FastAPI handles concurrent requests automatically via Uvicorn (worker processes)
- WebSocket streaming avoids blocking the orchestrator
- No global state—each turn is independent

### Latency Profile (typical)

| Stage | Time |
|-------|------|
| Extract (LLM) | 500ms–2s |
| Enhance | <1ms |
| Search (reranker) | 200–800ms |
| Resolve references (MySQL) | 100–500ms |
| Cart update (MySQL) | 50–200ms |
| **Total** | **~1–3s** |

To optimize:
- Use reranker with cached embeddings
- Batch MySQL queries where possible
- Run Uvicorn with multiple workers

---

## Deployment Checklist

- [ ] Environment variables set (ANTHROPIC_API_KEY, reranker endpoint, MySQL creds)
- [ ] MySQL schema initialized (catalog, cart, session tables)
- [ ] Frontend build passes type checking: `npm run build`
- [ ] Backend tests pass: `uv run pytest`
- [ ] Linting passes: `uv run ruff check src/` and `npm run lint`
- [ ] Type checking passes: `uv run mypy src/`
- [ ] API contract documented (Swagger available at `/docs`)
- [ ] Reranker endpoint is reachable and returns valid responses
- [ ] WebSocket streaming tested end-to-end

---

## References

- **Pydantic Docs:** https://docs.pydantic.dev/latest/
- **FastAPI Docs:** https://fastapi.tiangolo.com/
- **Claude API:** https://platform.openai.com/docs/guides/function-calling
- **Vite Guide:** https://vitejs.dev/guide/
- **UV Docs:** https://docs.astral.sh/uv/
