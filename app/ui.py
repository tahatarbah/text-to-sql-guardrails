"""Streamlit UI for Text-to-SQL with guardrails and hallucination reports."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import streamlit as st

# Ensure project root is on path when launched via `streamlit run app/ui.py`
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import get_settings
from app.db.connection import get_engine, ping
from app.db.schema import introspect_schema
from app.llm.ollama_client import OllamaClient
from app.pipeline import run_pipeline

st.set_page_config(
    page_title="Text-to-SQL Guardrails",
    page_icon="🛡️",
    layout="wide",
)

st.title("Text-to-SQL with Guardrails & Hallucination Detection")
st.caption("Fully local via Ollama · read-only SQL · schema-grounded checks")


@st.cache_resource
def _load_schema():
    return introspect_schema(get_engine())


def _status_badges() -> None:
    settings = get_settings()
    cols = st.columns(3)
    # DB
    db_ok, db_msg = False, "DATABASE_URL not set"
    if settings.database_url:
        try:
            db_ok = ping()
            db_msg = "Connected"
        except Exception as exc:
            db_msg = str(exc)
    cols[0].metric("Database", "OK" if db_ok else "Down", db_msg[:60])

    ollama = OllamaClient()
    ollama_ok = ollama.is_available()
    cols[1].metric("Ollama", "OK" if ollama_ok else "Down", settings.ollama.host)

    cols[2].metric(
        "Models",
        settings.ollama.sql_model,
        f"judge={settings.ollama.judge_model}",
    )


_status_badges()

settings = get_settings()
if not settings.database_url:
    st.error("Set `DATABASE_URL` in a `.env` file (see `.env.example`).")
    st.stop()

with st.sidebar:
    st.header("Schema browser")
    try:
        schema = _load_schema()
        st.success(f"Dialect: **{schema.dialect}** · {len(schema.tables)} tables")
        for tname in schema.table_names():
            info = schema.tables[tname]
            with st.expander(tname):
                for c in info.columns:
                    pk = " 🔑" if c.primary_key else ""
                    st.text(f"{c.name}: {c.type}{pk}")
        if st.button("Refresh schema"):
            _load_schema.clear()
            st.rerun()
    except Exception as exc:
        st.error(f"Schema load failed: {exc}")
        schema = None

st.subheader("Ask a question")
question = st.text_area(
    "Natural language question",
    placeholder="e.g. Show the top 10 customers by total order amount",
    height=100,
)
mode = st.radio(
    "Mode",
    options=["Validate and run", "Validate only"],
    horizontal=True,
)
execute = mode == "Validate and run"

if st.button("Run", type="primary", disabled=not question.strip()):
    with st.spinner("Generating SQL and running safety checks..."):
        result = run_pipeline(question, execute=execute, schema=schema)

    status_color = {
        "executed": "green",
        "validated": "blue",
        "blocked": "red",
    }.get(result.status, "gray")
    st.markdown(
        f"**Status:** :{status_color}[{result.status.upper()}]"
        + (
            f" · confidence **{result.confidence:.2f}**"
            if result.confidence is not None
            else ""
        )
    )

    if result.block_reasons:
        st.error("Blocked:\n\n" + "\n".join(f"- {r}" for r in result.block_reasons))

    tab_sql, tab_guard, tab_hall, tab_result = st.tabs(
        ["SQL", "Guardrails", "Hallucination", "Results"]
    )

    with tab_sql:
        if result.sql:
            st.code(result.sql, language="sql")
        if result.raw_sql and result.raw_sql != result.sql:
            st.caption("Raw model output (before guardrail normalization)")
            st.code(result.raw_sql, language="sql")
        if result.generation:
            st.json(
                {
                    "model": result.generation.get("model"),
                    "tables_in_prompt": result.generation.get("tables_used_in_prompt"),
                    "error": result.generation.get("error"),
                }
            )

    with tab_guard:
        if result.guardrails:
            ok = result.guardrails.get("ok")
            st.write("Passed" if ok else "Failed")
            for issue in result.guardrails.get("issues") or []:
                icon = "⚠️" if issue.get("severity") == "warning" else "⛔"
                st.write(f"{icon} `{issue.get('code')}` — {issue.get('message')}")
            if result.explain:
                st.subheader("EXPLAIN")
                if result.explain.get("ok"):
                    st.success("EXPLAIN succeeded")
                    if result.explain.get("plan"):
                        st.json(result.explain["plan"])
                else:
                    st.error(result.explain.get("error"))
        else:
            st.info("No guardrail report")

    with tab_hall:
        if result.schema_check:
            st.subheader("Schema identifier check")
            sc = result.schema_check
            st.write(
                f"{'Passed' if sc.get('ok') else 'Failed'} · "
                f"confidence {sc.get('confidence', 0):.2f}"
            )
            for issue in sc.get("issues") or []:
                st.write(f"- {issue.get('message')}")
            if sc.get("referenced_tables"):
                st.caption("Referenced tables: " + ", ".join(sc["referenced_tables"]))
        if result.judge:
            st.subheader("Faithfulness judge")
            j = result.judge
            if not j.get("enabled"):
                st.info("Judge disabled (JUDGE_MODE=off)")
            else:
                st.write(
                    f"faithful={j.get('faithful')} · score={j.get('score')} · "
                    f"blocked={j.get('blocked')}"
                )
                for issue in j.get("issues") or []:
                    st.write(f"- {issue}")
                if j.get("error"):
                    st.warning(j["error"])

    with tab_result:
        if result.result and result.result.get("ok"):
            cols = result.result.get("columns") or []
            rows = result.result.get("rows") or []
            if cols:
                df = pd.DataFrame(rows, columns=cols)
                st.dataframe(df, use_container_width=True)
                st.caption(f"{result.result.get('row_count', 0)} row(s) returned")
            else:
                st.info("Query returned no columns")
        elif result.status == "validated":
            st.info("Validation only — execution skipped.")
        elif result.result and result.result.get("error"):
            st.error(result.result["error"])
        else:
            st.info("No result rows")
