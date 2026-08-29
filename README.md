# QueryGuard — Text-to-SQL with Guardrails & Hallucination Detection

Fully local natural-language → SQL system. Uses **Ollama** for generation (and an optional faithfulness judge), connects to **Postgres or MySQL**, and **fail-closes** on unsafe or hallucinated SQL before execution.

## Features

- Schema-grounded SQL generation with cached introspection
- Guardrails via `sqlglot` (SELECT-only, LIMIT inject/cap, allowlists, dangerous clause/function blocks)
- Hallucination detection (identifier checks + optional Ollama judge)
- `EXPLAIN` dry-run + read-only capped execution
- Query history, example questions, editable SQL re-check
- **QueryGuard** web UI served from FastAPI (`/`)
- Full technical tutorial in [`docs/TECHNICAL_TUTORIAL.md`](docs/TECHNICAL_TUTORIAL.md)

## Quick start

```bash
docker compose up -d
docker compose exec ollama ollama pull sqlcoder:7b
docker compose exec ollama ollama pull llama3.2

python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
copy .env.example .env          # already points at Compose Postgres on :5433

uvicorn app.main:app --reload --host 127.0.0.1 --port 8020
```

Open **http://127.0.0.1:8020/** for the UI, or **http://127.0.0.1:8020/docs** for the API.

Postgres sample DB: `localhost:5433` / user·pass·db `textsql`  
Ollama: `localhost:11434`

## Main API

| Endpoint | Purpose |
|----------|---------|
| `POST /api/query` | Natural language → guarded pipeline |
| `POST /api/sql` | Validate / run edited SQL |
| `GET /api/schema` | Introspected schema |
| `GET /api/examples` | Suggested questions |
| `GET /api/history` | Recent queries |
| `GET /api/health` | Dependency status |

## Tests

```bash
pytest -q
```

## Learn the system

Read the full walkthrough: **[Technical Tutorial](docs/TECHNICAL_TUTORIAL.md)** — architecture, each pipeline stage, configuration, UI usage, threat model, and extension points.

Optional legacy Streamlit UI: `streamlit run app/ui.py`
