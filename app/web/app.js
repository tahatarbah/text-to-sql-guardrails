(() => {
  const $ = (sel) => document.querySelector(sel);

  const els = {
    question: $("#question"),
    runBtn: $("#runBtn"),
    validateOnly: $("#validateOnly"),
    skipJudge: $("#skipJudge"),
    statusPills: $("#statusPills"),
    schemaList: $("#schemaList"),
    schemaMeta: $("#schemaMeta"),
    refreshSchema: $("#refreshSchema"),
    historyList: $("#historyList"),
    clearHistory: $("#clearHistory"),
    exampleChips: $("#exampleChips"),
    pipelineTrack: $("#pipelineTrack"),
    stages: $("#stages"),
    alertBox: $("#alertBox"),
    resultGrid: $("#resultGrid"),
    sqlEditor: $("#sqlEditor"),
    statusBadge: $("#statusBadge"),
    confidenceLabel: $("#confidenceLabel"),
    safetyReport: $("#safetyReport"),
    hallReport: $("#hallReport"),
    resultTable: $("#resultTable"),
    resultMeta: $("#resultMeta"),
    copySql: $("#copySql"),
    rerunSql: $("#rerunSql"),
  };

  let lastQuestion = "";

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    let data = null;
    const text = await res.text();
    try {
      data = text ? JSON.parse(text) : null;
    } catch {
      data = { detail: text };
    }
    if (!res.ok) {
      const detail = data?.detail;
      const msg =
        typeof detail === "string"
          ? detail
          : detail?.message || JSON.stringify(detail) || res.statusText;
      throw new Error(msg);
    }
    return data;
  }

  function setBusy(busy) {
    els.runBtn.disabled = busy;
    els.rerunSql.disabled = busy;
    const label = els.runBtn.querySelector(".btn-label");
    const spinner = els.runBtn.querySelector(".btn-spinner");
    if (label) label.textContent = busy ? "Running…" : "Run pipeline";
    if (spinner) spinner.hidden = !busy;
  }

  function pill(key, text, ok) {
    const el = els.statusPills.querySelector(`[data-key="${key}"]`);
    if (!el) return;
    el.textContent = text;
    el.classList.toggle("ok", !!ok);
    el.classList.toggle("bad", ok === false);
  }

  async function loadHealth() {
    try {
      const h = await api("/api/health");
      pill("db", h.database?.ok ? "DB connected" : "DB down", !!h.database?.ok);
      pill("ollama", h.ollama?.ok ? "Ollama up" : "Ollama down", !!h.ollama?.ok);
      const models = h.ollama?.models || [];
      const want = h.ollama?.sql_model || "sql model";
      const has = models.some((m) => m === want || m.startsWith(`${want}:`) || m.startsWith(want.split(":")[0]));
      pill("model", has ? want : `${want} missing`, has);
    } catch (err) {
      pill("db", "API unreachable", false);
      pill("ollama", "—", false);
      pill("model", err.message, false);
    }
  }

  async function loadSchema(refresh = false) {
    els.schemaMeta.textContent = "Loading…";
    try {
      const data = refresh
        ? await api("/api/schema/refresh", { method: "POST" })
        : await api("/api/schema");
      els.schemaMeta.textContent = `${data.dialect} · ${data.tables.length} tables` +
        (data.cache?.age_sec != null ? ` · cache ${data.cache.age_sec}s` : "");
      els.schemaList.innerHTML = data.tables
        .map((t) => {
          const cols = t.columns
            .map(
              (c) =>
                `<div class="col-line">${c.name}: ${c.type}${c.primary_key ? " · PK" : ""}</div>`
            )
            .join("");
          return `<details class="schema-item"><summary>${t.name}</summary>${cols}</details>`;
        })
        .join("");
    } catch (err) {
      els.schemaMeta.textContent = err.message;
      els.schemaList.innerHTML = "";
    }
  }

  async function loadExamples() {
    try {
      const data = await api("/api/examples");
      els.exampleChips.innerHTML = (data.examples || [])
        .slice(0, 6)
        .map((q) => `<button type="button" class="chip" data-q="${encodeURIComponent(q)}">${q}</button>`)
        .join("");
    } catch {
      els.exampleChips.innerHTML = "";
    }
  }

  async function loadHistory() {
    try {
      const data = await api("/api/history");
      const items = data.items || [];
      if (!items.length) {
        els.historyList.innerHTML = `<p class="muted">No queries yet.</p>`;
        return;
      }
      els.historyList.innerHTML = items
        .map((h) => {
          const when = new Date((h.ts || 0) * 1000).toLocaleTimeString();
          return `<div class="history-item" data-q="${encodeURIComponent(h.question || "")}" data-sql="${encodeURIComponent(h.sql || "")}">
            <p class="q">${escapeHtml(h.question || "")}</p>
            <div class="meta"><span>${h.status}</span><span>${when}</span></div>
          </div>`;
        })
        .join("");
    } catch {
      els.historyList.innerHTML = `<p class="muted">History unavailable.</p>`;
    }
  }

  function escapeHtml(s) {
    return String(s)
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function renderStages(stages) {
    if (!stages?.length) {
      els.pipelineTrack.hidden = true;
      return;
    }
    els.pipelineTrack.hidden = false;
    els.stages.innerHTML = stages
      .map(
        (s) => `<li class="${s.ok ? "ok" : "bad"}">
          <strong>${escapeHtml(s.name)}</strong>
          <span>${escapeHtml(s.detail || "")}</span>
          <div class="muted">${s.ms ?? 0} ms</div>
        </li>`
      )
      .join("");
  }

  function renderAlert(data) {
    const reasons = data.block_reasons || [];
    const warnings = data.warnings || [];
    if (!reasons.length && !warnings.length) {
      els.alertBox.hidden = true;
      return;
    }
    els.alertBox.hidden = false;
    els.alertBox.classList.toggle("warn", !reasons.length && !!warnings.length);
    const lines = [
      ...reasons.map((r) => `Blocked: ${r}`),
      ...warnings.map((w) => `Warning: ${w}`),
    ];
    els.alertBox.innerHTML = lines.map((l) => `<div>${escapeHtml(l)}</div>`).join("");
  }

  function renderSafety(data) {
    const g = data.guardrails || {};
    const issues = g.issues || [];
    const items = [
      `<div class="item ${g.ok ? "ok" : "bad"}">Guardrails: ${g.ok ? "passed" : "failed"}</div>`,
      ...issues.map(
        (i) =>
          `<div class="item ${i.severity === "warning" ? "warn" : "bad"}">${escapeHtml(i.code)} — ${escapeHtml(i.message)}</div>`
      ),
    ];
    if (data.explain) {
      items.push(
        `<div class="item ${data.explain.ok ? "ok" : "bad"}">EXPLAIN: ${
          data.explain.ok ? "ok" : escapeHtml(data.explain.error || "failed")
        }</div>`
      );
    }
    if (data.timings?.total_ms != null) {
      items.push(`<div class="item">Total time: ${data.timings.total_ms} ms</div>`);
    }
    els.safetyReport.innerHTML = items.join("") || `<div class="muted">No report</div>`;
  }

  function renderHall(data) {
    const sc = data.schema_check || {};
    const j = data.judge || {};
    const items = [
      `<div class="item ${sc.ok ? "ok" : "bad"}">Schema identifiers: ${
        sc.ok ? "ok" : "issues found"
      } · confidence ${(sc.confidence ?? 0).toFixed(2)}</div>`,
      ...(sc.issues || []).map((i) => `<div class="item bad">${escapeHtml(i.message)}</div>`),
    ];
    if (j.enabled === false) {
      items.push(`<div class="item">Judge: off / skipped</div>`);
    } else if (j.enabled) {
      items.push(
        `<div class="item ${j.blocked ? "bad" : j.faithful ? "ok" : "warn"}">Judge score ${(j.score ?? 0).toFixed(2)} · faithful=${j.faithful}</div>`
      );
      (j.issues || []).forEach((msg) => {
        items.push(`<div class="item warn">${escapeHtml(msg)}</div>`);
      });
    }
    els.hallReport.innerHTML = items.join("");
  }

  function renderTable(data) {
    const result = data.result;
    if (!result?.ok) {
      els.resultMeta.textContent =
        data.status === "validated" ? "Validation only — not executed" : "No rows";
      els.resultTable.innerHTML = `<p class="muted" style="padding:0.75rem">No result set.</p>`;
      return;
    }
    const cols = result.columns || [];
    const rows = result.rows || [];
    els.resultMeta.textContent = `${result.row_count ?? rows.length} row(s)`;
    if (!cols.length) {
      els.resultTable.innerHTML = `<p class="muted" style="padding:0.75rem">Empty result.</p>`;
      return;
    }
    const head = cols.map((c) => `<th>${escapeHtml(c)}</th>`).join("");
    const body = rows
      .map(
        (r) =>
          `<tr>${r.map((cell) => `<td>${escapeHtml(cell == null ? "" : cell)}</td>`).join("")}</tr>`
      )
      .join("");
    els.resultTable.innerHTML = `<table><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
  }

  function renderResult(data) {
    els.resultGrid.hidden = false;
    els.sqlEditor.value = data.sql || data.raw_sql || "";
    els.statusBadge.textContent = data.status || "—";
    els.statusBadge.className = `badge ${data.status || ""}`;
    els.confidenceLabel.textContent =
      data.confidence != null ? `confidence ${Number(data.confidence).toFixed(2)}` : "";
    renderStages(data.stages || []);
    renderAlert(data);
    renderSafety(data);
    renderHall(data);
    renderTable(data);
  }

  async function runPipeline() {
    const question = els.question.value.trim();
    if (!question) {
      els.question.focus();
      return;
    }
    lastQuestion = question;
    setBusy(true);
    try {
      const data = await api("/api/query", {
        method: "POST",
        body: JSON.stringify({
          question,
          execute: !els.validateOnly.checked,
          skip_judge: els.skipJudge.checked,
        }),
      });
      renderResult(data);
      await loadHistory();
      await loadHealth();
    } catch (err) {
      els.alertBox.hidden = false;
      els.alertBox.classList.remove("warn");
      els.alertBox.textContent = err.message;
    } finally {
      setBusy(false);
    }
  }

  async function rerunSql() {
    const sql = els.sqlEditor.value.trim();
    if (!sql) return;
    setBusy(true);
    try {
      const data = await api("/api/sql", {
        method: "POST",
        body: JSON.stringify({
          sql,
          question: lastQuestion || els.question.value.trim() || "(manual SQL)",
          execute: !els.validateOnly.checked,
          skip_judge: true,
        }),
      });
      renderResult(data);
      await loadHistory();
    } catch (err) {
      els.alertBox.hidden = false;
      els.alertBox.classList.remove("warn");
      els.alertBox.textContent = err.message;
    } finally {
      setBusy(false);
    }
  }

  els.runBtn.addEventListener("click", runPipeline);
  els.rerunSql.addEventListener("click", rerunSql);
  els.refreshSchema.addEventListener("click", () => loadSchema(true));
  els.clearHistory.addEventListener("click", async () => {
    await api("/api/history", { method: "DELETE" });
    await loadHistory();
  });
  els.copySql.addEventListener("click", async () => {
    await navigator.clipboard.writeText(els.sqlEditor.value || "");
    els.copySql.textContent = "Copied";
    setTimeout(() => (els.copySql.textContent = "Copy"), 1200);
  });
  els.question.addEventListener("keydown", (e) => {
    if ((e.ctrlKey || e.metaKey) && e.key === "Enter") {
      e.preventDefault();
      runPipeline();
    }
  });
  els.exampleChips.addEventListener("click", (e) => {
    const btn = e.target.closest(".chip");
    if (!btn) return;
    els.question.value = decodeURIComponent(btn.dataset.q || "");
    els.question.focus();
  });
  els.historyList.addEventListener("click", (e) => {
    const item = e.target.closest(".history-item");
    if (!item) return;
    els.question.value = decodeURIComponent(item.dataset.q || "");
    if (item.dataset.sql) {
      els.sqlEditor.value = decodeURIComponent(item.dataset.sql);
      els.resultGrid.hidden = false;
    }
  });

  loadHealth();
  loadSchema();
  loadExamples();
  loadHistory();
  setInterval(loadHealth, 20000);
})();
