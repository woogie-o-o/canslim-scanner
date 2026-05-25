(function () {
  const state = { layer: "pass", data: null, polling: false, diffData: null };

  function fmtPct(v) { return v == null ? "—" : (v * 100).toFixed(1) + "%"; }
  function fmtMcap(v) {
    if (v == null) return "—";
    if (v >= 1e9) return (v / 1e9).toFixed(2) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(0) + "M";
    return v.toString();
  }

  function renderTable() {
    const tbl = document.getElementById("mb-table");
    if (!state.data) { tbl.innerHTML = "<tr><td>로딩 중…</td></tr>"; return; }
    const rows = state.data[state.layer] || [];
    if (state.layer === "diff") {
      if (!state.diffData) {
        fetch("/api/multibagger/diff").then(r => r.json()).then(d => { state.diffData = d; renderTable(); });
        tbl.innerHTML = "<tr><td>DIFF 데이터 로딩 중…</td></tr>";
        return;
      }
      if (!state.diffData.stats.available) {
        tbl.innerHTML = '<tr><td>DIFF 데이터 준비 안 됨. <code>python -m web_app.multibagger_backtest</code> 실행 필요.</td></tr>';
        return;
      }
      const items = state.diffData.baggers;
      let html = "<thead><tr><th>#</th><th>Ticker</th><th>5y Multiple</th><th>분류</th><th>탈락 게이트</th></tr></thead><tbody>";
      items.forEach((r, i) => {
        const failBadges = (r.fail_gates || []).map(g => `<span class="mb-badge fail">${g}</span>`).join("");
        html += `<tr><td>${i+1}</td><td><b>${r.ticker}</b></td><td>${r.multiple}×</td><td>${r.classify}</td><td>${failBadges}</td></tr>`;
      });
      tbl.innerHTML = html + "</tbody>";
      return;
    }
    if (!rows.length) { tbl.innerHTML = "<tr><td>해당 레이어에 종목 없음</td></tr>"; return; }
    const isWatch = state.layer === "watch";
    let html = "<thead><tr><th>#</th><th>Ticker</th><th>Score</th><th>시총</th><th>ROIC</th><th>FCF Yld / P/B</th><th>EBITDA YoY−Rev YoY</th><th>52w↓</th><th>섹터</th>" + (isWatch ? "<th>부족</th>" : "") + "</tr></thead><tbody>";
    rows.forEach((r, i) => {
      const valGap = (r.ebitda_yoy != null && r.revenue_yoy != null) ? fmtPct(r.ebitda_yoy - r.revenue_yoy) : "—";
      const valuation = r.fcf_yield != null ? fmtPct(r.fcf_yield) + " / " + (r.pb != null ? r.pb.toFixed(1) : "—") : (r.pb != null ? "PB " + r.pb.toFixed(1) : "—");
      const shortGates = isWatch ? "<td>" + (r.gates_failed.concat(r.gates_missing)).map(g => `<span class="mb-badge fail">${g}</span>`).join("") + "</td>" : "";
      html += `<tr onclick="location.href='/detail/${r.ticker}'" style="cursor:pointer">
        <td>${i+1}</td><td><b>${r.ticker}</b></td><td>${r.score.toFixed(1)}</td>
        <td>${fmtMcap(r.market_cap)}</td><td>${fmtPct(r.roic)}</td><td>${valuation}</td>
        <td>${valGap}</td><td>${fmtPct(r.from_52w_high)}</td><td>${r.sector || "—"}</td>${shortGates}
      </tr>`;
    });
    html += "</tbody>";
    tbl.innerHTML = html;
  }

  function renderMeta() {
    if (!state.data) return;
    const m = state.data.meta || {};
    document.getElementById("mb-meta").textContent =
      `Universe ${m.universe_n||0} · Candidates ${m.candidates_n||0} · PASS ${m.pass_n||0} · WATCH ${m.watch_n||0}` +
      (m.dgs10_pct ? ` · DGS10 ${m.dgs10_pct.toFixed(2)}%` : "");
  }

  async function load() {
    const resp = await fetch("/api/multibagger");
    state.data = await resp.json();
    renderMeta(); renderTable();
    if (resp.headers.get("X-Warming-In-Progress") === "true" && !state.polling) {
      state.polling = true;
      setTimeout(() => { state.polling = false; load(); }, 30000);
    }
  }

  document.querySelectorAll(".mb-tabs button").forEach(btn => {
    btn.addEventListener("click", () => {
      document.querySelectorAll(".mb-tabs button").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.layer = btn.dataset.layer;
      renderTable();
    });
  });

  document.getElementById("mb-advanced-toggle").addEventListener("click", async () => {
    const panel = document.getElementById("mb-advanced");
    if (panel.hasAttribute("hidden")) {
      const resp = await fetch("/api/multibagger/thresholds");
      const th = await resp.json();
      panel.innerHTML = "<pre>" + JSON.stringify(th, null, 2) + "</pre><small>현재 임계값. 튜닝 UI는 추후 기능.</small>";
      panel.removeAttribute("hidden");
    } else {
      panel.setAttribute("hidden", "");
    }
  });

  load();
})();
