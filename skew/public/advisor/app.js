"use strict";

// ---------- helpers ----------
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const money = (v, dp = 0) => v == null ? "—" : "$" + Number(v).toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
const big = (v) => {
  if (v == null) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return "$" + (v / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return "$" + (v / 1e9).toFixed(1) + "B";
  if (a >= 1e6) return "$" + (v / 1e6).toFixed(1) + "M";
  return money(v, 2);
};
const pct = (v, dp = 1, signed = false) => v == null ? "—" : (signed && v > 0 ? "+" : "") + (v * 100).toFixed(dp) + "%";
const num = (v, dp = 1, suffix = "") => v == null ? "—" : Number(v).toFixed(dp) + suffix;
const asymTag = (a) => a == null ? '<span class="tag bad">n/a</span>'
  : `<span class="tag ${a >= 3 ? "good" : a >= 1.5 ? "mid" : "bad"}">${a >= 10 ? "10x+" : a.toFixed(1) + "x"}</span>`;
const storage = {
  get(k) { try { return JSON.parse(localStorage.getItem(k)); } catch (e) { return null; } },
  set(k, v) { try { localStorage.setItem(k, JSON.stringify(v)); } catch (e) { /* storage full or blocked */ } },
};
const PALETTE = ["#8b5cf6", "#06b6d4", "#10b981", "#f59e0b", "#3b82f6", "#ec4899", "#14b8a6", "#f97316", "#a3e635", "#e879f9", "#38bdf8", "#facc15", "#fb7185", "#4ade80", "#c084fc", "#2dd4bf"];

async function api(path, body, timeoutMs = 200000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  let res;
  try {
    res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal: controller.signal });
  } catch (e) {
    throw new Error(e.name === "AbortError" ? "timed out" : e.message);
  } finally {
    clearTimeout(timer);
  }
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (e) { /* not JSON */ }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return res.json();
}

async function pool(items, size, fn) {
  const results = new Array(items.length);
  let next = 0;
  await Promise.all(Array.from({ length: Math.min(size, items.length) }, async () => {
    while (next < items.length) {
      const i = next++;
      results[i] = await fn(items[i], i);
    }
  }));
  return results;
}

// ---------- state ----------
const state = {
  universe: null,      // { built_at, stocks: [...] }
  byTicker: {},
  details: {},         // ticker -> full detail JSON (fetched lazily)
  run: null,           // { profile, shortlist, analyses, allocation, memo, funnel, at }
  charts: {},
};

// ---------- profile ----------
function readProfile() {
  return {
    amount: Number($("amount").value) || 100000,
    core_pct: Number($("core").value) / 100,
    etf: $("etf").value,
    num_stocks: Math.max(1, Number($("num").value) || 8),
    max_stock_pct: Number($("maxstock").value) / 100,
    max_sector_pct: Number($("maxsector").value) / 100,
    min_asymmetry: Number($("minasym").value),
  };
}

function bindProfile() {
  const outs = [["core", "core-out", (v) => v + "%"], ["maxstock", "maxstock-out", (v) => v + "%"],
                ["maxsector", "maxsector-out", (v) => v + "%"], ["minasym", "minasym-out", (v) => Number(v).toFixed(2).replace(/0$/, "") + "x"]];
  for (const [input, out, f] of outs) {
    const update = () => { $(out).textContent = f($(input).value); };
    $(input).addEventListener("input", update);
    update();
  }
}

// ---------- pipeline UI ----------
const STEPS = [
  ["data", "Load data", "S&P 500 snapshot"],
  ["screen", "Screen", "Score & asymmetry"],
  ["ai", "AI analyst", "Read filings & news"],
  ["allocate", "Allocate", "Live prices & caps"],
  ["memo", "Explain", "Write & verify memo"],
];

function renderPipeline() {
  $("pipeline").innerHTML = STEPS.map(([id, t, s], i) => `
    <div class="step" id="step-${id}">
      <span class="n">0${i + 1}</span><span class="t">${t}</span>
      <span class="s" id="step-${id}-s">${s}</span>
      <div class="bar"><i id="step-${id}-bar"></i></div>
    </div>`).join("");
}

function setStep(id, status, text, progress) {
  const el = $(`step-${id}`);
  el.classList.remove("active", "done", "error");
  if (status) el.classList.add(status);
  if (text != null) $(`step-${id}-s`).textContent = text;
  if (progress != null) $(`step-${id}-bar`).style.width = Math.round(progress * 100) + "%";
}

function renderFunnel(f) {
  const parts = [
    [f.universe, "in S&P 500"],
    [f.complete, "with full data"],
    [f.asymmetric, `asymmetry ≥ ${f.min}x`],
    [f.shortlist, "AI-analysed"],
    [f.picked ?? "…", "in portfolio"],
  ];
  $("funnel").innerHTML = parts.map(([n, l], i) =>
    (i ? '<div class="arrow">→</div>' : "") + `<div><b>${n}</b><span>${l}</span></div>`).join("");
}

// ---------- the run ----------
function asymBonus(a) { return 10 * Math.min(Math.max(a || 0, 0), 5) / 5; }

async function runPipeline() {
  const profile = readProfile();
  const stocks = state.universe.stocks;
  $("run").disabled = true;
  $("hero").classList.add("hidden");
  $("pipeline-card").classList.remove("hidden");
  $("error").classList.add("hidden");
  $("results").classList.add("hidden");
  renderPipeline();
  $("pipeline-sub").textContent = `Building a portfolio of ${money(profile.amount)} for the client…`;
  const t0 = performance.now();

  try {
    setStep("data", "done", `${stocks.length} stocks loaded`, 1);

    // 02 Screen (deterministic, instant)
    setStep("screen", "active", "Scoring…", 0.3);
    const complete = stocks.filter((s) => s.asymmetry != null && s.price);
    const asymmetric = complete.filter((s) => s.asymmetry >= profile.min_asymmetry);
    const ranked = [...asymmetric].sort((a, b) => (b.score + asymBonus(b.asymmetry)) - (a.score + asymBonus(a.asymmetry)));
    const shortlist = ranked.slice(0, profile.num_stocks + 4).map((s) => s.ticker);
    const funnel = { universe: stocks.length, complete: complete.length, asymmetric: asymmetric.length, shortlist: shortlist.length, min: profile.min_asymmetry };
    renderFunnel(funnel);
    setStep("screen", "done", `${asymmetric.length} pass, top ${shortlist.length} shortlisted`, 1);

    // 03 AI analyst, in parallel, cards fill in live
    const analyses = {};
    state.run = { profile, shortlist, analyses, allocation: null, memo: null, funnel, at: new Date().toISOString() };
    showResultsShell();
    renderCards();
    let done = 0;
    setStep("ai", "active", `Reading 0/${shortlist.length}…`, 0);
    await pool(shortlist, shortlist.length, async (ticker) => {
      try {
        const detail = await loadDetail(ticker);
        analyses[ticker] = await api("/api/analyze", detail);
      } catch (e) {
        analyses[ticker] = { error: e.message };
      }
      done++;
      setStep("ai", "active", `Reading ${done}/${shortlist.length} · ${ticker}`, done / shortlist.length);
      renderCards();
    });
    const failedAI = shortlist.filter((t) => analyses[t]?.error).length;
    setStep("ai", "done", failedAI ? `${shortlist.length - failedAI} analysed, ${failedAI} failed` : `${shortlist.length} companies analysed`, 1);

    // 04 Allocate
    setStep("allocate", "active", "Fetching live prices…", 0.4);
    const candidates = complete.map((s) => ({
      ticker: s.ticker, name: s.name, sector: s.sector, price: s.price, quant_score: s.score, asymmetry: s.asymmetry,
      ai_adjustment: analyses[s.ticker]?.conviction_adjustment?.value || 0,
    }));
    const allocation = await api("/api/portfolio", { profile, candidates });
    state.run.allocation = allocation;
    funnel.picked = allocation.holdings.filter((h) => h.kind === "stock").length;
    renderFunnel(funnel);
    setStep("allocate", "done", `${funnel.picked} stocks + ${profile.etf}, ${allocation.live_prices} live prices`, 1);
    renderResults();

    // 05 Memo
    setStep("memo", "active", "Writing memo…", 0.4);
    const slim = {};
    for (const h of allocation.holdings) {
      const a = analyses[h.ticker];
      if (a && !a.error) slim[h.ticker] = { one_liner: a.one_liner, bear_points: a.bear_points.slice(0, 1) };
    }
    state.run.memo = await api("/api/memo", { profile, allocation, analyses: slim });
    const check = state.run.memo.check;
    setStep("memo", "done", check.ok ? `All ${check.total} figures verified` : `${check.unverified.length} figure(s) unverified`, 1);
    renderMemo();

    const secs = ((performance.now() - t0) / 1000).toFixed(0);
    $("pipeline-sub").textContent = `Done in ${secs}s · ${new Date().toLocaleTimeString()}`;
    storage.set("skew:lastRun", state.run);
    updateLastRunNote();
  } catch (e) {
    const active = document.querySelector(".step.active");
    if (active) active.classList.replace("active", "error");
    $("error").textContent = "Something went wrong: " + e.message;
    $("error").classList.remove("hidden");
  } finally {
    $("run").disabled = false;
  }
}

async function loadDetail(ticker) {
  if (!state.details[ticker]) {
    const res = await fetch(`data/stocks/${encodeURIComponent(ticker)}.json`);
    if (!res.ok) throw new Error(`No data for ${ticker}`);
    state.details[ticker] = await res.json();
  }
  return state.details[ticker];
}

// ---------- results ----------
function showResultsShell() {
  $("results").classList.remove("hidden");
  const ready = !!state.run?.allocation;
  for (const id of ["kpis", "alloc-card", "memo-card", "excluded-card"]) $(id).classList.toggle("hidden", !ready);
}

function holdingColor(i) { return PALETTE[i % PALETTE.length]; }

function renderResults() {
  const { allocation, profile } = state.run;
  if (!allocation) return;
  showResultsShell();
  const stocks = allocation.holdings.filter((h) => h.kind === "stock");
  const stockTotal = stocks.reduce((a, h) => a + h.amount, 0) || 1;
  const wEps = stocks.reduce((a, h) => a + (state.byTicker[h.ticker]?.eps_cagr_3y ?? 0) * h.amount, 0) / stockTotal;
  const wScore = stocks.reduce((a, h) => a + h.final_score * h.amount, 0) / stockTotal;
  const kpis = [
    ["Invested", money(allocation.invested), `${money(allocation.cash, 2)} cash left (whole shares)`],
    ["Holdings", `${stocks.length} + ${profile.etf}`, `${pct(profile.core_pct, 0)} index core`],
    ["Weighted asymmetry", allocation.weighted_asymmetry != null ? (allocation.weighted_asymmetry >= 10 ? "10x+" : allocation.weighted_asymmetry.toFixed(1) + "x") : "—", "bull upside ÷ bear downside"],
    ["Avg EPS growth", pct(wEps), `3-yr CAGR · avg score ${wScore.toFixed(0)}`],
  ];
  $("kpis").innerHTML = kpis.map(([l, v, n]) => `<div class="card kpi"><div class="label">${l}</div><div class="value">${v}</div><div class="note">${n}</div></div>`).join("");

  $("donut-total").textContent = money(allocation.invested);
  $("alloc-note").textContent = `Prices as of ${new Date(state.run.at).toLocaleString()}`;
  $("holdings").innerHTML = allocation.holdings.map((h, i) => `
    <tr class="${h.kind === "stock" ? "clickable" : ""}" data-ticker="${esc(h.ticker)}">
      <td><span class="swatch" style="background:${holdingColor(i)}"></span><span class="tk">${esc(h.ticker)}</span><div class="nm">${esc(h.name)}</div></td>
      <td>${h.kind === "etf" ? '<span class="tag core">Index core</span>' : esc(h.sector)}</td>
      <td class="r num">${money(h.amount)}</td>
      <td><span class="wbar"><i style="width:${Math.min(100, h.weight * 100 / 0.4 * 1)}%;background:${holdingColor(i)}"></i></span><span class="num">${pct(h.weight)}</span></td>
      <td class="r num">${h.shares}</td>
      <td class="r">${h.kind === "stock" ? asymTag(h.asymmetry) : '<span class="muted">—</span>'}</td>
      <td class="r num">${h.kind === "stock" ? h.final_score.toFixed(1) : "—"}</td>
    </tr>`).join("");
  $("holdings").querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", () => openDrawer(tr.dataset.ticker)));

  if (state.charts.donut) state.charts.donut.destroy();
  if (window.Chart) {
    state.charts.donut = new Chart($("donut"), {
      type: "doughnut",
      data: { labels: allocation.holdings.map((h) => h.ticker), datasets: [{ data: allocation.holdings.map((h) => h.amount), backgroundColor: allocation.holdings.map((_, i) => holdingColor(i)), borderWidth: 0, hoverOffset: 6 }] },
      options: { cutout: "68%", maintainAspectRatio: false, plugins: { legend: { display: false },
        tooltip: { callbacks: { label: (c) => ` ${c.label}: ${money(c.raw)} (${pct(c.raw / allocation.invested)})` } } } },
    });
  }

  const excluded = allocation.excluded
    .filter((e) => state.run.shortlist.includes(e.ticker) || e.reason.startsWith("Ranked"))
    .sort((a, b) => (b.quant_score || 0) - (a.quant_score || 0)).slice(0, 12);
  $("excluded").innerHTML = excluded.map((e) => `
    <tr class="clickable" data-ticker="${esc(e.ticker)}">
      <td><span class="tk">${esc(e.ticker)}</span><div class="nm">${esc(e.name)}</div></td>
      <td>${esc(e.sector)}</td><td class="r num">${num(e.quant_score)}</td><td class="r">${asymTag(e.asymmetry)}</td>
      <td class="muted">${esc(e.reason)}</td></tr>`).join("") || '<tr><td colspan="5" class="muted">None</td></tr>';
  $("excluded").querySelectorAll("tr.clickable").forEach((tr) => tr.addEventListener("click", () => openDrawer(tr.dataset.ticker)));
  renderCards();
}

function renderMemo() {
  const memo = state.run.memo;
  if (!memo) { $("memo").innerHTML = '<p class="skeleton"><span class="spinner"></span> Writing the client memo…</p>'; $("verify").innerHTML = ""; return; }
  $("memo").innerHTML = `<h2 style="text-transform:none;font:700 18px var(--sans);letter-spacing:-.01em">${esc(memo.title)}</h2>` +
    memo.paragraphs.map((p) => `<p>${esc(p)}</p>`).join("");
  const c = memo.check;
  $("verify").innerHTML = c.ok
    ? `<span class="verify ok">✓ All ${c.total} figures verified against source data</span>`
    : `<span class="verify warn">⚠ Unverified: ${c.unverified.map(esc).join(", ")}</span>`;
}

function sparkline(values, up) {
  if (!values || values.length < 2) return "";
  const min = Math.min(...values), max = Math.max(...values), w = 220, h = 34;
  const pts = values.map((v, i) => `${(i / (values.length - 1) * w).toFixed(1)},${(h - 3 - (v - min) / (max - min || 1) * (h - 6)).toFixed(1)}`).join(" ");
  const color = up ? "var(--up)" : "var(--down)";
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><polyline points="${pts}" fill="none" stroke="${color}" stroke-width="1.6" vector-effect="non-scaling-stroke"/></svg>`;
}

function asymBar(down, up, years = 5) {
  const d = Math.max(0, -(down ?? 0)), u = Math.max(0, up ?? 0), total = d + u || 1;
  return `<div class="asym">
    <div class="asym-track">
      <div class="dn" style="left:0;width:${d / total * 100}%"></div>
      <div class="upb" style="left:${d / total * 100}%;width:${u / total * 100}%"></div>
      <div class="now" style="left:calc(${d / total * 100}% - 1px)"></div>
    </div>
    <div class="asym-labels"><span class="down">${down >= 0 ? "no modelled downside" : "bear " + pct(down, 0)}</span><span class="up">bull ${pct(up, 0, true)}</span></div>
  </div>`;
}

function renderCards() {
  const run = state.run;
  if (!run) return;
  const picked = new Set((run.allocation?.holdings || []).filter((h) => h.kind === "stock").map((h) => h.ticker));
  const order = [...run.shortlist].sort((a, b) => (picked.has(b) - picked.has(a)));
  $("cards").innerHTML = order.map((t) => {
    const s = state.byTicker[t] || {}, a = run.analyses[t];
    const up = s.spark && s.spark.length > 1 && s.spark[s.spark.length - 1] >= s.spark[0];
    let body;
    if (!a) body = '<div class="skeleton"><span class="spinner"></span> AI reading earnings release & news…</div>';
    else if (a.error) body = `<div class="line down">AI analysis unavailable: ${esc(a.error).slice(0, 90)}</div>`;
    else body = `<div class="line">${esc(a.one_liner)}</div>`;
    const adj = a && !a.error ? a.conviction_adjustment.value : null;
    return `<div class="stock ${picked.has(t) ? "picked" : ""}" data-ticker="${esc(t)}">
      <div class="top"><div><span class="tk">${esc(t)}</span><div class="nm">${esc(s.name)}</div></div>
        ${picked.has(t) ? '<span class="badge">In portfolio</span>' : ""}</div>
      ${sparkline(s.spark, up)}
      ${asymBar(s.downside, s.upside)}
      ${body}
      <div class="stats"><span>Score <b>${num(s.score)}</b></span><span>Asym ${asymTag(s.asymmetry)}</span>
        <span>AI <b class="${adj > 0 ? "up" : adj < 0 ? "down" : ""}">${adj == null ? "…" : (adj > 0 ? "+" : "") + adj}</b></span></div>
    </div>`;
  }).join("");
  $("cards").querySelectorAll(".stock").forEach((el) => el.addEventListener("click", () => openDrawer(el.dataset.ticker)));
}

// ---------- deep-dive drawer ----------
function citeChips(ticker, ids) {
  return (ids || []).map((id) => `<span class="cite" data-t="${esc(ticker)}" data-id="${esc(id)}">${esc(id)}</span>`).join("");
}
function pointList(ticker, items, cls) {
  if (!items || !items.length) return '<p class="muted">None.</p>';
  return `<ul class="points ${cls}">${items.map((p) => `<li>${esc(p.text)}${citeChips(ticker, p.cites)}</li>`).join("")}</ul>`;
}

async function openDrawer(ticker) {
  $("drawer").classList.add("open");
  $("overlay").classList.add("open");
  $("drawer").setAttribute("aria-hidden", "false");
  $("drawer-head").innerHTML = `<div><h2>${esc(ticker)}</h2><p class="sub">Loading…</p></div><button class="close" id="close">×</button>`;
  $("drawer-body").innerHTML = '<div class="skeleton"><span class="spinner"></span> Loading…</div>';
  $("close").onclick = closeDrawer;
  let d;
  try { d = await loadDetail(ticker); } catch (e) { $("drawer-body").innerHTML = `<div class="err">${esc(e.message)}</div>`; return; }
  const a = state.run?.analyses?.[ticker];
  const m = d.metrics, sc = d.scenarios, ref = d.reference || {};
  const holding = state.run?.allocation?.holdings.find((h) => h.ticker === ticker);

  $("drawer-head").innerHTML = `
    <div>
      <div class="eyebrow">${esc(d.sector)} · ${esc(d.industry)}</div>
      <h2>${esc(d.name)} <span class="muted num" style="font-size:15px">${esc(ticker)}</span></h2>
      <p class="sub"><span class="num" style="color:var(--ink);font-size:16px">${money(m.price, 2)}</span>
        · market cap ${big(ref.market_cap)} · 52w ${money(ref.low_52w, 0)}–${money(ref.high_52w, 0)}
        ${holding ? ` · <b style="color:var(--accent)">${money(holding.amount)} in portfolio (${holding.shares} shares)</b>` : ""}</p>
    </div>
    <div style="display:flex;gap:8px;align-items:center">
      <a class="theme-btn" href="/#${encodeURIComponent(ticker)}" style="display:inline-flex;align-items:center;text-decoration:none;color:var(--accent)">RESEARCH OVERVIEW →</a>
      <button class="close" id="close">×</button>
    </div>`;
  $("close").onclick = closeDrawer;

  const metricCard = (label, value, src) => `<div class="metric"><div class="label">${label}</div><div class="value">${value}</div>${src ? `<a href="${esc(src)}" target="_blank" rel="noopener">source ↗</a>` : '<span class="muted" style="font-size:11px">computed</span>'}</div>`;
  const secUrl = d.sources.financials;
  const scenarioHtml = sc ? `
    ${asymBar(sc.downside, sc.upside)}
    <div class="scen">
      ${["bear", "base", "bull"].map((k) => `<div class="${k}">${k.toUpperCase()} · ${sc.years}y<b>${money(sc.cases[k].price, 0)}</b>${pct(sc.cases[k].return, 0, true)} · EPS ${pct(sc.cases[k].growth, 0)}/yr · exit P/E ${sc.cases[k].exit_pe.toFixed(1)}x</div>`).join("")}
    </div>
    <p class="sub" style="margin-top:10px">Asymmetry <b class="num" style="color:var(--ink)">${sc.asymmetry >= 10 ? "10x+" : sc.asymmetry.toFixed(1) + "x"}</b> · growth input: ${sc.growth_basis} CAGR ${pct(sc.growth_input)}</p>`
    : '<p class="muted">Not enough data to model scenarios (needs positive EPS).</p>';

  let aiHtml;
  if (!a) aiHtml = `<p class="muted">Not in this run's AI shortlist. <button class="link-btn" id="analyse-now">Run AI analysis now →</button></p>`;
  else if (a.error) aiHtml = `<div class="err">AI analysis unavailable: ${esc(a.error)}</div>`;
  else aiHtml = `
    <div class="ai-head">
      <span class="pill">Moat: <b>${esc(a.moat.rating)}</b>${citeChips(ticker, a.moat.cites)}</span>
      <span class="pill">Sentiment: <b class="${a.sentiment.score > 0.15 ? "up" : a.sentiment.score < -0.15 ? "down" : ""}">${a.sentiment.score.toFixed(2)}</b>${citeChips(ticker, a.sentiment.cites)}</span>
      <span class="pill">Conviction adj: <b class="${a.conviction_adjustment.value > 0 ? "up" : a.conviction_adjustment.value < 0 ? "down" : ""}">${a.conviction_adjustment.value > 0 ? "+" : ""}${a.conviction_adjustment.value}</b></span>
    </div>
    <p style="font-size:15px;margin:0 0 6px">${esc(a.one_liner)}</p>
    <p class="sub">${esc(a.moat.reason)}</p>
    <h3 style="margin-top:16px">Earnings release takeaways</h3>${pointList(ticker, a.earnings_takeaways, "neutral")}
    <div class="two" style="margin-top:16px">
      <div><h3>Bull case</h3>${pointList(ticker, a.bull_points, "bull")}</div>
      <div><h3>Bear case</h3>${pointList(ticker, a.bear_points, "bear")}</div>
    </div>
    <h3 style="margin-top:16px">What would make the client sell</h3>${pointList(ticker, a.thesis_breakers, "breakers")}
    <p class="sub" style="margin-top:12px">Conviction: ${esc(a.conviction_adjustment.reason)}</p>`;

  const rec = ref.earnings_record || {};
  const an = ref.analyst || {};
  const insiders = (ref.insiders || []).slice(0, 6);
  const news = (ref.news || []).slice(0, 6);

  $("drawer-body").innerHTML = `
    <section class="card"><div class="eyebrow">AI analyst · cited</div><div style="margin-top:10px">${aiHtml}</div></section>
    <section class="card"><h3>5-year scenarios</h3>${scenarioHtml}</section>
    <section class="card"><h3>Fundamentals · SEC 10-K</h3>
      <div class="metrics">
        ${metricCard("P/E", num(m.pe, 1, "x"), null)}
        ${metricCard("PEG", num(m.peg, 2), null)}
        ${metricCard("Revenue CAGR 3y", pct(m.revenue_cagr_3y), secUrl)}
        ${metricCard("EPS CAGR 3y", pct(m.eps_cagr_3y), secUrl)}
        ${metricCard("Operating margin", pct(m.operating_margin), secUrl)}
        ${metricCard("FCF margin", pct(m.fcf_margin), secUrl)}
        ${metricCard("Debt / equity", m.negative_equity ? "neg. equity" : num(m.debt_to_equity, 2, "x"), secUrl)}
        ${metricCard("Quality score", `${num(d.score.total)}<span class="muted" style="font-size:12px"> /100</span>`, null)}
        ${metricCard("Diluted EPS", money(m.eps, 2), secUrl)}
      </div>
      <div class="two" style="margin-top:14px"><div class="chart-box"><canvas id="rev-chart"></canvas></div><div class="chart-box"><canvas id="eps-chart"></canvas></div></div>
    </section>
    <section class="card"><h3>Market view</h3>
      <dl class="kv">
        <dt>Analyst consensus</dt><dd>${esc(an.rating || "—")} · ${an.count ?? "—"} analysts</dd>
        <dt>12-mo target (mean)</dt><dd>${money(an.mean, 2)} <span class="${(an.upside_pct ?? 0) >= 0 ? "up" : "down"}">(${an.upside_pct != null ? (an.upside_pct > 0 ? "+" : "") + an.upside_pct + "%" : "—"})</span></dd>
        <dt>Target range</dt><dd>${money(an.low, 0)} – ${money(an.high, 0)}</dd>
        <dt>Earnings record</dt><dd>${rec.quarters_count ? `beat ${rec.beats}/${rec.quarters_count} quarters · avg surprise ${rec.avg_surprise_pct}%` : "—"}</dd>
        <dt>Next earnings</dt><dd>${esc(ref.next_earnings?.date || "—")}</dd>
        <dt>Trend</dt><dd>${ref.technicals?.sma200 ? (m.price > ref.technicals.sma200 ? '<span class="up">above</span>' : '<span class="down">below</span>') + " 200-day average" : "—"} · RSI ${num(ref.technicals?.rsi, 0)}</dd>
      </dl>
    </section>
    <section class="card"><h3>Insider activity · SEC Form 4</h3>
      ${insiders.length ? `<div class="table-scroll"><table><thead><tr><th>Date</th><th>Insider</th><th>Type</th><th class="r">Value</th></tr></thead><tbody>
      ${insiders.map((t) => `<tr><td class="num">${esc(t.date)}</td><td>${esc(t.insider)}<div class="nm">${esc(t.position)}</div></td><td class="${t.code === "P" ? "up" : t.code === "S" ? "down" : "muted"}">${t.url ? `<a href="${esc(t.url)}" target="_blank" rel="noopener" style="color:inherit">${esc(t.type)} ↗</a>` : esc(t.type)}</td><td class="r num">${big(t.value)}</td></tr>`).join("")}
      </tbody></table></div>` : '<p class="muted">No recent filings.</p>'}
    </section>
    <section class="card news"><h3>Recent news</h3>
      ${news.map((n) => `<a href="${esc(n.url)}" target="_blank" rel="noopener">${esc(n.title)}<small>${esc(n.publisher || "")} · ${esc(n.date || "")}</small></a>`).join("") || '<p class="muted">No recent headlines.</p>'}
    </section>
    <section class="card"><h3>Sources</h3>
      <div style="display:flex;gap:8px;flex-wrap:wrap">
        <a class="src-chip" href="/#${encodeURIComponent(ticker)}" style="border-color:var(--accent);color:var(--accent)">Full stock research overview →</a>
        <a class="src-chip" href="${esc(secUrl)}" target="_blank" rel="noopener">SEC 10-K filings ↗</a>
        ${d.earnings_release ? `<a class="src-chip" href="${esc(d.earnings_release.url)}" target="_blank" rel="noopener">8-K earnings release (${esc(d.earnings_release.filed)}) ↗</a>` : ""}
        <a class="src-chip" href="${esc(d.sources.price)}" target="_blank" rel="noopener">Nasdaq quote ↗</a>
        <span class="src-chip">Stocks-Research snapshot · ${esc((state.universe.built_at || "").slice(0, 10))}</span>
      </div>
    </section>`;

  const an2 = $("analyse-now");
  if (an2) an2.onclick = async () => {
    an2.outerHTML = '<span class="skeleton"><span class="spinner"></span> Analysing…</span>';
    try {
      const result = await api("/api/analyze", d);
      if (state.run) { state.run.analyses[ticker] = result; storage.set("skew:lastRun", state.run); }
      else { state.run = { analyses: { [ticker]: result }, shortlist: [] }; }
      openDrawer(ticker);
    } catch (e) { $("drawer-body").insertAdjacentHTML("afterbegin", `<div class="err">${esc(e.message)}</div>`); }
  };
  drawFinancialCharts(d);
}

function drawFinancialCharts(d) {
  if (!window.Chart) return;
  for (const k of ["rev", "eps"]) if (state.charts[k]) state.charts[k].destroy();
  const css = getComputedStyle(document.documentElement);
  const ink3 = css.getPropertyValue("--ink-3").trim(), line = css.getPropertyValue("--line").trim();
  const opts = (title, fmt) => ({
    maintainAspectRatio: false,
    plugins: { legend: { display: false }, title: { display: true, text: title, color: ink3, font: { family: "JetBrains Mono", size: 11 } },
      tooltip: { callbacks: { label: (c) => " " + fmt(c.raw) } } },
    scales: { x: { ticks: { color: ink3 }, grid: { display: false } }, y: { ticks: { color: ink3, callback: fmt }, grid: { color: line } } },
  });
  const rev = d.financials.revenue.slice(-5), eps = d.financials.eps.slice(-5);
  state.charts.rev = new Chart($("rev-chart"), { type: "bar",
    data: { labels: rev.map((r) => r[0].slice(0, 4)), datasets: [{ data: rev.map((r) => r[1]), backgroundColor: "#06b6d4", borderRadius: 5 }] },
    options: opts("REVENUE (FY)", big) });
  state.charts.eps = new Chart($("eps-chart"), { type: "bar",
    data: { labels: eps.map((r) => r[0].slice(0, 4)), datasets: [{ data: eps.map((r) => r[1]), backgroundColor: "#8b5cf6", borderRadius: 5 }] },
    options: opts("DILUTED EPS (FY, split-adj.)", (v) => "$" + Number(v).toFixed(2)) });
}

function closeDrawer() {
  $("drawer").classList.remove("open");
  $("overlay").classList.remove("open");
  $("drawer").setAttribute("aria-hidden", "true");
}

// ---------- citation tooltips ----------
function bindTooltips() {
  const tip = $("tip");
  document.addEventListener("mouseover", (e) => {
    const c = e.target.closest(".cite");
    if (!c) return;
    const fact = state.run?.analyses?.[c.dataset.t]?.facts?.find((f) => f.id === c.dataset.id);
    if (!fact) return;
    const value = fact.value.length > 320 ? fact.value.slice(0, 320) + "…" : fact.value;
    tip.innerHTML = `<b>${esc(fact.id)} · ${esc(fact.label)}</b>${esc(value)}<div class="src">${esc(fact.source)}</div>`;
    tip.classList.remove("hidden");
    const r = c.getBoundingClientRect();
    tip.style.left = Math.max(8, Math.min(window.innerWidth - 370, r.left - 20)) + "px";
    tip.style.top = (r.bottom + 8 + tip.offsetHeight > window.innerHeight ? r.top - tip.offsetHeight - 8 : r.bottom + 8) + "px";
  });
  document.addEventListener("mouseout", (e) => { if (e.target.closest(".cite")) tip.classList.add("hidden"); });
  document.addEventListener("click", (e) => {
    const c = e.target.closest(".cite");
    if (!c) return;
    const fact = state.run?.analyses?.[c.dataset.t]?.facts?.find((f) => f.id === c.dataset.id);
    if (fact && /^https?:/.test(fact.source)) window.open(fact.source, "_blank", "noopener");
  });
}

// ---------- boot ----------
function updateLastRunNote() {
  $("last-run-note").textContent = state.run?.at ? `Last run ${new Date(state.run.at).toLocaleString()}` : "";
}

function bindTheme() {
  $("theme-btn").onclick = () => {
    const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("skew:theme", next); } catch (e) { /* ignore */ }
    if (state.run?.allocation) renderResults();
  };
}

async function boot() {
  bindProfile();
  bindTheme();
  bindTooltips();
  $("overlay").onclick = closeDrawer;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });
  $("run").onclick = runPipeline;

  fetch("/api/health").then((r) => r.json()).then((h) => {
    $("model-pill").innerHTML = `<span class="status-dot" style="${h.ai ? "" : "background:var(--down);box-shadow:none"}"></span>AI: ${esc(h.model)}`;
    $("etf").innerHTML = Object.entries(h.etfs).map(([k, v]) => `<option value="${esc(k)}" title="${esc(v)}">${esc(k)}</option>`).join("");
  }).catch(() => {
    $("model-pill").textContent = "AI: offline";
    $("etf").innerHTML = ["VOO", "VT", "QQQ"].map((k) => `<option>${k}</option>`).join("");
  });

  try {
    state.universe = await (await fetch("data/universe.json")).json();
    for (const s of state.universe.stocks) state.byTicker[s.ticker] = s;
    $("data-pill").innerHTML = `<span class="status-dot"></span>${state.universe.count} S&amp;P 500 stocks · data ${esc(state.universe.built_at.slice(0, 10))}`;
  } catch (e) {
    $("data-pill").innerHTML = '<span class="status-dot" style="background:var(--down)"></span>Dataset missing';
    $("run").disabled = true;
    return;
  }

  // Restore the last run so a demo can reopen instantly without the network
  const last = storage.get("skew:lastRun");
  if (last?.allocation) {
    state.run = last;
    $("hero").classList.add("hidden");
    $("pipeline-card").classList.remove("hidden");
    renderPipeline();
    STEPS.forEach(([id]) => setStep(id, "done", null, 1));
    renderFunnel(last.funnel);
    $("pipeline-sub").textContent = `Restored run from ${new Date(last.at).toLocaleString()} · press Build portfolio to refresh`;
    renderResults();
    renderMemo();
    updateLastRunNote();
  }
}

boot();
