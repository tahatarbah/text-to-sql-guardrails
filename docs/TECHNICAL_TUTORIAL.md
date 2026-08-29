# QueryGuard Technical Tutorial

A complete guide to the **Text-to-SQL Interface with Guardrails and Hallucination Detection** system (product name: **QueryGuard**).

This document explains what the system does, how it is built, how data flows through each safety stage, how to run it locally, and how to extend it.

---

## 1. Problem statement

Large language models can turn natural language into SQL, but unconstrained generation is dangerous:

1. **Destructive SQL** — `DROP`, `DELETE`, or multi-statement injection.
2. **Hallucinated schema** — inventing tables/columns that do not exist.
3. **Silent wrong answers** — SQL that runs but does not answer the question.
4. **Unbounded result sets** — missing `LIMIT` that overload the database.

QueryGuard solves this with a **fail-closed pipeline**: every query must pass deterministic guardrails, schema checks, optional LLM judging, and an `EXPLAIN` dry-run before any rows are returned. Generation and judging run **fully locally via Ollama** (no cloud LLM APIs).

---

## 2. System overview

```
User (QueryGuard UI)
        │
        ▼
   FastAPI (app/main.py)
        │
        ▼
   Pipeline (app/pipeline.py)
        │
        ├── Schema cache / introspection  → Postgres or MySQL
        ├── Ollama SQL generator          → sqlcoder (configurable)
        ├── Guardrails (sqlglot AST)
        ├── Hallucination checks
        │     ├── Deterministic schema identifier check
        │     └── Optional Ollama faithfulness judge
        ├── EXPLAIN dry-run
        └── Read-only execute (capped rows)
```

### Design principles

| Principle | Implementation |
|-----------|----------------|
| Fail closed | Any hard failure → `status: blocked`, no rows |
| Local-first | Ollama only; no OpenAI/Anthropic required |
| Read-only by default | AST bans DML/DDL; DB session is read-only |
| Schema-grounded | Prompt includes introspected tables/columns/FKs |
| Observable | Per-stage timings, history, UI safety reports |

---

## 3. Repository map

```
app/
  main.py                 # FastAPI routes + static UI mount
  pipeline.py             # Orchestrates generate → validate → execute
  config.py               # .env + config.yaml settings
  ui.py                   # Legacy Streamlit UI (optional)
  web/                    # QueryGuard SPA (HTML/CSS/JS)
  db/
    connection.py         # SQLAlchemy engine
    schema.py             # SchemaGraph + introspection + table ranking
    executor.py           # EXPLAIN + read-only execute
  llm/
    ollama_client.py      # HTTP client for Ollama
    prompts.py            # System/user prompts
    generator.py          # NL → SQL
  guardrails/
    policy.py             # Allow/deny lists
    sql_validator.py      # sqlglot validation + LIMIT inject/cap
  hallucination/
    schema_check.py       # Unknown table/column detection
    judge.py              # Optional faithfulness JSON judge
  services/
    schema_cache.py       # TTL schema cache
    history.py            # In-memory query history
    examples.py           # Example questions from schema
scripts/
  seed.sql                # Demo ecommerce schema
  smoke_query.py          # CLI smoke test
tests/                    # Guardrail + hallucination unit tests
docker-compose.yml        # Postgres (5433) + Ollama
docs/TECHNICAL_TUTORIAL.md
```

---

## 4. Pipeline stages (deep dive)

### Stage A — Schema context

`introspect_schema()` uses SQLAlchemy inspection to load tables, columns, PKs, and FKs into a `SchemaGraph`.

`select_relevant_tables()` ranks tables by keyword overlap with the question (no embeddings) and keeps the top N (`schema_top_n`, default 12). That keeps prompts small on large databases.

Results are served through `SchemaCache` (default TTL 300s) so repeated questions do not re-introspect every time. Refresh via `POST /api/schema/refresh`.

### Stage B — SQL generation

`generate_sql()` calls Ollama with:

- **System prompt**: SELECT/WITH only, use listed schema only, include LIMIT, return SQL only.
- **User prompt**: dialect + schema text + question.

`extract_sql()` strips markdown fences and leading commentary so models that chat still yield runnable SQL.

### Stage C — Guardrails (`sqlglot`)

`validate_sql()` parses the AST and enforces:

| Check | Behavior |
|-------|----------|
| Empty SQL | Block |
| Multiple statements | Block |
| Non-SELECT roots (`INSERT`, `DROP`, …) | Block |
| Nested DML/DDL nodes | Block |
| Dangerous functions (`pg_read_file`, `sleep`, …) | Block |
| `INTO OUTFILE` / `FOR UPDATE` text clauses | Block |
| Table allowlist (optional) | Block unknown tables |
| Missing `LIMIT` | Inject `LIMIT max_rows` (warning) |
| `LIMIT` too large | Cap to `max_rows` (warning) |

Normalized SQL is what later stages see.

### Stage D — Hallucination detection

**Deterministic (primary)** — `check_against_schema()` walks tables/columns in the AST, resolves aliases, and flags unknown identifiers. Confidence starts at `1.0` and is penalized per issue.

**Semantic judge (secondary)** — `judge_sql()` asks Ollama for JSON:

```json
{ "faithful": true, "score": 0.0-1.0, "issues": [] }
```

`JUDGE_MODE`:

- `block` — below threshold blocks execution
- `warn` — surfaces warning, still may execute
- `off` — skipped

UI and API also support `skip_judge: true` for faster iteration.

### Stage E — EXPLAIN dry-run

Runs `EXPLAIN` (Postgres JSON format when available) inside a read-only transaction, then rolls back. Catches syntax/semantic DB errors without returning data.

### Stage F — Safe execute

Only if all hard checks pass:

1. Begin transaction  
2. Set read-only (`SET TRANSACTION READ ONLY` / MySQL equivalent)  
3. Apply statement timeout (Postgres)  
4. `fetchmany(max_rows)`  
5. **Always roll back** (never commit)

---

## 5. API reference

Base URL: `http://127.0.0.1:8020` (or your chosen port).

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/` | QueryGuard web UI |
| `GET` | `/api/health` | DB + Ollama + models + safety config |
| `GET` | `/api/schema?refresh=` | Introspected schema |
| `POST` | `/api/schema/refresh` | Force cache refresh |
| `GET` | `/api/examples` | Suggested questions |
| `GET` | `/api/history` | Recent queries |
| `DELETE` | `/api/history` | Clear history |
| `POST` | `/api/query` | NL → full pipeline |
| `POST` | `/api/sql` | Validate/execute edited SQL |
| `POST` | `/api/export.csv` | Run question and download CSV |
| `GET` | `/docs` | OpenAPI (Swagger) |

### `POST /api/query` body

```json
{
  "question": "Top products by quantity sold",
  "execute": true,
  "skip_judge": false
}
```

### Important response fields

- `status`: `executed` | `validated` | `blocked`
- `sql`: guardrail-normalized SQL
- `block_reasons` / `warnings`
- `guardrails`, `schema_check`, `judge`, `explain`, `result`
- `stages`: ordered list with `ok`, `detail`, `ms`
- `timings`: per-stage milliseconds including `total_ms`
- `confidence`: combined schema (+ judge) score

Legacy aliases `/health`, `/schema`, `/query` remain for compatibility.

---

## 6. Configuration

### `.env`

```env
DATABASE_URL=postgresql+psycopg://textsql:textsql@localhost:5433/textsql
OLLAMA_HOST=http://localhost:11434
SQL_MODEL=sqlcoder:7b
JUDGE_MODEL=llama3.2
MAX_ROWS=100
QUERY_TIMEOUT_SEC=30
JUDGE_MODE=block
JUDGE_THRESHOLD=0.7
# TABLE_ALLOWLIST=customers,orders,products
```

### `config.yaml`

Defaults for Ollama temperature/timeout, schema top-N, API bind host/port. Environment variables override YAML where both exist.

---

## 7. Local setup walkthrough

### 7.1 Dependencies

- Python 3.11+
- Docker Desktop (recommended for Postgres + Ollama)
- Or native Postgres/MySQL + native Ollama

### 7.2 Start infrastructure

```bash
docker compose up -d
docker compose exec ollama ollama pull sqlcoder:7b
docker compose exec ollama ollama pull llama3.2
```

Compose maps Postgres to **host port 5433** (to avoid clashing with an existing local 5432) and Ollama to **11434**. Seed data creates `customers`, `products`, `orders`, `order_items`.

### 7.3 Python environment

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

### 7.4 Run the app

```bash
uvicorn app.main:app --reload --host 127.0.0.1 --port 8020
```

Open:

- UI: http://127.0.0.1:8020/
- API docs: http://127.0.0.1:8020/docs

Optional legacy Streamlit UI:

```bash
streamlit run app/ui.py
```

### 7.5 Smoke test

```bash
python scripts/smoke_query.py "List customers and cities" --validate-only
pytest -q
```

---

## 8. Using the QueryGuard UI

1. Confirm status pills: **DB connected**, **Ollama up**, model present.  
2. Browse schema in the left rail; refresh if you changed the DB.  
3. Click an example chip or type a question.  
4. Optionally enable **Validate only** or **Skip judge**.  
5. Click **Run pipeline** (or Ctrl/Cmd+Enter).  
6. Inspect:
   - Pipeline stage timeline (with timings)
   - Generated SQL (editable)
   - Safety + hallucination reports
   - Result table  
7. Edit SQL and click **Re-check edited SQL** to re-run guardrails/EXPLAIN/execute without calling the generator.  
8. History stores recent questions for quick replay.

---

## 9. Safety model (threats and controls)

| Threat | Control |
|--------|---------|
| `DROP TABLE` / `DELETE` | AST deny-list; never reaches DB |
| `SELECT 1; DROP TABLE x` | Multi-statement rejection |
| `SELECT ... INTO OUTFILE` | Banned clause scan |
| Unknown table `spaceships` | Schema check blocks |
| Missing LIMIT | Auto-inject |
| Runaway query time | Statement timeout |
| Accidental writes via driver | Read-only transaction + rollback |
| Unfaithful but valid SQL | Optional judge gate |

**Important:** Guardrails reduce risk significantly but are not a substitute for database privileges. Production deployments should also use a **read-only DB user**.

---

## 10. Extending the system

### Add a new dangerous function

Edit `DANGEROUS_FUNCTIONS` in `app/guardrails/policy.py` and add a unit test in `tests/test_guardrails.py`.

### Swap models

Change `SQL_MODEL` / `JUDGE_MODEL` in `.env`. Any Ollama-compatible model works; SQL-specialized models (e.g. sqlcoder) usually produce cleaner queries.

### Point at your own database

Set `DATABASE_URL` to your Postgres or MySQL URI. Prefer a read-only role. Use `TABLE_ALLOWLIST` to expose only approved tables.

### Improve table retrieval

Replace `select_relevant_tables()` with embeddings or BM25 if schemas are huge. Keep the rest of the pipeline unchanged.

### Persist history

`QueryHistory` is in-memory. Swap `app/services/history.py` for SQLite/Redis without changing the API contract.

---

## 11. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|--------------|-----|
| DB pill red | Wrong `DATABASE_URL` / DB down | Check Compose (`5433`), credentials |
| Ollama down | Container/process stopped | `docker compose up -d ollama` |
| Model missing | Not pulled | `docker compose exec ollama ollama pull sqlcoder:7b` |
| Always blocked by judge | Weak local model / strict threshold | `JUDGE_MODE=warn` or `skip_judge` |
| Port already allocated | Another app on 8000/5432 | Use 8020 / Compose 5433 |
| EXPLAIN fails | Dialect mismatch / bad SQL | Inspect `block_reasons` and normalized SQL |

---

## 12. Suggested learning path

1. Run Compose + UI and execute an example question.  
2. Intentionally ask for a fake table; confirm schema check blocks.  
3. Paste `DELETE FROM customers` into the SQL editor; confirm guardrails block.  
4. Read `pipeline.py` top to bottom once with a successful response open.  
5. Modify `MAX_ROWS` and observe LIMIT injection/capping.  
6. Write one new guardrail unit test.

---

## 13. Version notes

- **v0.1** — Core pipeline, Streamlit UI, unit tests  
- **v0.2** — Schema cache, history, SQL re-check API, QueryGuard web UI, stage timings, technical tutorial  

When you change safety behavior, update this tutorial’s Stage C/D tables and add regression tests.
