# Skew — AI Portfolio Analyst

**Microsoft × LSEG Student AI Hackathon 2026 · Build the AI Analyst**

Skew is a Streamlit research demo that allocates **US$100,000** for a long-horizon,
fundamentals-driven client using live public data, deterministic quant scores,
five-year asymmetry scenarios, cited Azure OpenAI research, constrained whole-share
allocation, and a client memo with numeric occurrence verification.

**Educational hackathon project. Not financial advice.**

---

## 1. The Client

| | |
|---|---|
| **Name** | Patient Asymmetric Investor |
| **Archetype** | Long-horizon fundamentals client |
| **Mandate** | Index core for ballast + high-conviction satellites where realistic five-year upside meaningfully exceeds downside |
| **Constraints** | 5+ year horizon · fundamentals first · core–satellite · asymmetry ≥ 1.5× · stock/sector caps |

Default: **30% VOO** core, **8** satellites, **15%** stock cap, **35%** sector cap,
universe of ~25 large/mid-cap names across sectors.

## 2. The Data

| Source | What we use |
|---|---|
| **Yahoo Finance** (`yfinance`) | Price, sector, market cap, P/E, 52-week range, fallback news, US 10Y yield (`^TNX`) |
| **SEC EDGAR** | Ticker→CIK map, US-GAAP company facts (10-K/FY), submissions, 8-K item 2.02 EX-99.1 earnings text |
| **Finnhub** (optional) | Company news headlines when `FINNHUB_API_KEY` is set |

Every field carries provider + public URL. Ticker JSON is cached 24h under `data/cache/`.
AI outputs are cached by facts digest under `data/ai_cache/` until **Refresh data + AI**.

## 3. The AI Logic

1. Rank by quantitative score (growth, quality, balance sheet, valuation).
2. Build a cited **FACTS** block (F/N/E ids) for the top 12 names.
3. Azure OpenAI returns structured JSON: earnings takeaways, sentiment, moat,
   bull/bear points, thesis breakers, conviction overlay (−10…+10) — every bullet cites facts.
4. Schema validation + one retry; failures become explicit “unavailable” with zero overlay.
5. One memo call explains the allocation; a regex **number check** flags unverified figures.

Endpoint defaults to the hackathon Azure OpenAI-compatible host (overridable via env).

## 4. The Recommendation & “Why”

- Exact dollar allocation to ETF + satellites (whole shares) with leftover cash.
- Signature KPI: **weighted asymmetry** (bull upside ÷ |bear loss|).
- Portfolio posture meter (educational concentration / aggression gauge).
- Deep-dive score waterfall: pillars → AI overlay → asymmetry bonus → final score.
- Source ledger with clickable fact citations and downloadable audit JSON.

## 5. Live Demo Ready

- Wide Streamlit UI with branded hero, client mandate pills, Plotly charts.
- Sidebar controls for amount, core %, ETF, N, caps, universe.
- Last run persists to `data/last_run.json` and reloads offline after restart.
- Progress bar; per-ticker failures never crash the whole run.

---

## Setup

```powershell
cd build
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
copy .env.example .env
# Edit .env: SEC_USER_AGENT and OPENAI_API_KEY
streamlit run app.py
```

macOS / Linux:

```bash
cd build
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
streamlit run app.py
```

Open http://localhost:8501

## Environment variables

| Variable | Required? | Meaning |
|---|---|---|
| `SEC_USER_AGENT` | For new SEC fetches | `"Your Name email@example.com"` |
| `OPENAI_API_KEY` | For AI + memo | Azure OpenAI-compatible API key |
| `AZURE_OPENAI_ENDPOINT` | No | Defaults to hackathon endpoint |
| `AZURE_OPENAI_API_VERSION` | No | Default `2024-12-01-preview` |
| `LLM_MODEL` | No | Default `gpt-6.1-sol` |
| `FINNHUB_API_KEY` | No | Prefer Finnhub news over Yahoo |

A missing AI key keeps the quantitative pipeline working. A missing SEC agent blocks
new fetches but still allows viewing a saved run.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Demo tips

1. Warm the cache once before presenting (`Run analysis`).
2. Restart Streamlit and confirm the saved recommendation appears offline.
3. Pitch path: Recommendation KPIs → posture meter → Deep dive waterfall + SEC citation → memo badge → Method.
4. Prefer **Run analysis** during the pitch; use **Refresh** only if live fetch is the point.

## Assumptions

- Client archetype defaults to the Patient Asymmetric Investor when unspecified.
- Public Yahoo/SEC data is sufficient for the demo (no paid LSEG terminal required).
- Whole-share rounding and hard caps may leave cash — treated as a feature, not a bug.
- Macro 10Y yield is context only and does not enter the optimizer.
