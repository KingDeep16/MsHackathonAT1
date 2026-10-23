Build "Skew", a Streamlit web app that recommends how to invest US$100,000 for one specific client and explains why. It is a hackathon demo (Microsoft x LSEG, judged live in an 8-minute presentation), so it must run reliably, look clean, and make every number traceable to public data. Educational project, not financial advice: show that line in the app footer.

# The client

A long-term, fundamentals-driven individual investor with an asymmetric mindset: they want businesses where the realistic upside is much larger than the realistic downside, held for 5+ years. Their portfolio is core-satellite: a broad index fund core for diversification (default 30%), plus 6–10 high-conviction individual stocks. They care about revenue growth, EPS growth, margins, balance-sheet strength, reasonable valuation (P/E relative to growth), what management said in the latest earnings report, and news sentiment.

# Stack

- Python 3.11+, Streamlit, pandas, plotly, yfinance, requests, openai. Pin versions in requirements.txt.
- LLM via the `openai` package's `AzureOpenAI` client: endpoint `https://app-uoaihack2ix6ak.azurewebsites.net`, api_version `2024-12-01-preview`, key from env `OPENAI_API_KEY`, model name from env `LLM_MODEL` defaulting to `gpt-6.1-sol`.
- Optional env `FINNHUB_API_KEY`: if set, use Finnhub company news; otherwise use yfinance news.
- Env `SEC_USER_AGENT` (e.g. "Name email@example.com"), required by SEC EDGAR; show a clear error if missing.
- Put all config (endpoint, model, default universe, defaults) in `config.py`.

# Pipeline (separate modules, each testable)

1. **Universe** – default list of ~25 large/mid-cap US tickers across sectors (e.g. MSFT, GOOGL, AMZN, NVDA, ASML, V, MA, COST, LLY, NVO, UNH, ADBE, CRM, NOW, INTU, ISRG, SPGI, MCO, AXP, BKNG, TSM, AVGO, META, AMD, NFLX). Editable in the sidebar.

2. **Data fetch** (`data_sources.py`) – per ticker, cached as JSON in `data/cache/{ticker}.json` with a fetch timestamp and a source tag on every field. Use cache if younger than 24h unless "Refresh data" is clicked. Fetch tickers concurrently (ThreadPoolExecutor, max 5 workers; respect SEC's 10 req/s). One ticker failing must not break the others — record the error and continue.
   - yfinance: price, sector, market cap, trailing P/E, forward P/E, 52-week high/low, recent news headlines.
   - SEC EDGAR (no key): map ticker→CIK via `https://www.sec.gov/files/company_tickers.json`; from `https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json` pull the last 5 fiscal years (10-K, FY) of Revenues (try `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`, `SalesRevenueNet`), `EarningsPerShareDiluted`, `OperatingIncomeLoss`, `NetIncomeLoss`, `NetCashProvidedByUsedInOperatingActivities`, `PaymentsToAcquirePropertyPlantAndEquipment`, `LongTermDebt` / `LongTermDebtNoncurrent`, `StockholdersEquity`. Foreign filers without data: mark fields missing, don't crash.
   - Latest earnings release: from `https://data.sec.gov/submissions/CIK##########.json`, find the most recent 8-K whose items include 2.02, open its filing index (`https://www.sec.gov/Archives/edgar/data/{cik}/{accession_no_dashes}/index.json`), fetch the exhibit 99.1 file, strip HTML to text, keep the first ~12,000 characters. Store the filing URL as the source.
   - News: up to 10 recent headlines with date, publisher and URL.

3. **Quant metrics and score** (`scoring.py`, pure functions, deterministic) – revenue 3y CAGR, EPS 3y CAGR, operating margin and its 3y trend, free-cash-flow margin, debt/equity, PEG (trailing P/E ÷ EPS CAGR %). Score 0–100 from four equally weighted pillars: Growth, Quality, Balance sheet, Valuation. Missing data lowers confidence, never crashes. Show the formula in a "Method" tab.

4. **Scenarios — the asymmetry** (`scoring.py`) – 5-year bear/base/bull price targets from fundamentals, all deterministic:
   - EPS path: bear = half of EPS CAGR (floored at 0%), base = EPS CAGR capped at 20%, bull = EPS CAGR capped at 35%.
   - Exit P/E: bear = min(current P/E, 15), base = min(current P/E, 25), bull = current P/E capped at 35.
   - Upside = bull/price − 1, downside = bear/price − 1. **Asymmetry ratio** = upside ÷ |downside| (cap display at 10×). This is the app's signature metric — show it prominently.

5. **AI analyst** (`ai_analyst.py`) – run for the top 12 tickers by quant score, concurrently. Send the model the ticker's computed metrics, scenarios, news headlines and earnings-release text as a JSON "facts" block, each item with an id (e.g. `F3`, `N2`, `E1`). Require JSON output (`response_format={"type":"json_object"}`) with:
   - `earnings_takeaways` (3 bullets from the release: guidance, segment trends, margin commentary)
   - `sentiment` (-1 to 1) with a one-line reason
   - `moat` ("none" | "narrow" | "wide") with reason
   - `bull_points`, `bear_points` (2–4 each)
   - `thesis_breakers` (what would make the client sell)
   - `conviction_adjustment` (integer -10 to +10) with reason
   - every bullet must cite fact ids it relies on, e.g. `["F3","E1"]`.
   The system prompt must forbid inventing numbers: any figure must come from the facts block. Validate the JSON; on failure retry once, then fall back to "AI analysis unavailable" for that ticker.

6. **Allocation** (`allocation.py`, deterministic) – final score = quant score + conviction adjustment + asymmetry bonus (up to +10, scaled by asymmetry ratio). Index core % (sidebar, default 30%) goes to a chosen ETF (sidebar: VOO, VT, QQQ; default VOO). The rest goes to the top N (sidebar, default 8) by final score, weighted by final score, with a max of 15% of total per stock and 35% per sector (redistribute excess). Convert to whole shares at current price; show leftover cash. Exclude any stock with asymmetry ratio below 1.5 or missing price.

7. **Client memo** – one LLM call writes a ~250-word plain-English memo to the client explaining the allocation, using only the computed allocation and analyses as facts. Then run a **number check**: extract every number in the memo with a regex and verify each appears (within rounding) in the facts sent; show a green "All figures verified against source data" badge or list the unverified numbers in amber.

# UI (Streamlit, wide layout, clean, minimal)

- Sidebar: client profile (amount $100,000, index core %, ETF, number of stocks, max per stock, max per sector), universe editor, "Run analysis" and "Refresh data" buttons, data timestamp.
- Tab "Recommendation": headline KPIs (total invested, # holdings, weighted asymmetry ratio, weighted EPS CAGR); allocation donut (plotly); table (ticker, sector, $ amount, shares, weight, final score, asymmetry ratio); the client memo with its verification badge.
- Tab "Deep dive": select a ticker → metrics cards, 5-year revenue/EPS bar chart, bear/base/bull scenario chart against current price, AI analysis with clickable source citations (hover/expander shows the cited fact and its URL).
- Tab "Screened out": tickers excluded and why (low asymmetry, missing data, fetch error).
- Tab "Method": the pipeline in five short steps, scoring formulas, data sources, limitations.
- Show a progress bar during runs. Persist the last run in `st.session_state` and to `data/last_run.json` so the demo can reload instantly without network.

# Quality bar

- `tests/test_scoring.py` and `tests/test_allocation.py` with unittest covering CAGR, PEG, scenarios, caps and redistribution, and that allocations sum to the input amount minus leftover cash.
- README: what it does, setup, env vars, `streamlit run app.py`, and a "Demo tips" section (run once before presenting so the cache is warm).
