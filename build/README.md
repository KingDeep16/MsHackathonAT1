# Skew

A Streamlit investment-research demo for a long-term, fundamentals-driven
individual allocating US$100,000 to an index core and high-conviction satellites.

Skew combines public financial observations, deterministic quantitative scores,
five-year asymmetry scenarios, source-cited AI research, constrained whole-share
allocations, and a client memo with a numeric occurrence check.

**Educational project, not financial advice.**

## Prerequisites

- Python 3.11 or newer.
- Internet access for a new analysis.
- A descriptive SEC user agent containing your name and email.
- An Azure-compatible API key for AI research and the client memo, if desired.
- A writable project directory for local caches.

No paid financial-data subscription is required. This implementation uses public
Yahoo/SEC data and optional Finnhub news; it does not claim an LSEG connection.

## Setup

Run commands from the project root.

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt

export SEC_USER_AGENT="Your Name your.email@example.com"
export OPENAI_API_KEY="your-api-key"

streamlit run app.py
```

### Windows PowerShell

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt

$env:SEC_USER_AGENT="Your Name your.email@example.com"
$env:OPENAI_API_KEY="your-api-key"

streamlit run app.py
```

Open the local URL printed by Streamlit, normally http://localhost:8501.

If the `streamlit` executable is not on PATH, use:

```bash
python -m streamlit run app.py
```

Environment variables are read when the server starts. Restart the server after
changing them. A `.env` file is not automatically loaded.

## Environment variables

| Variable | Required? | Meaning |
|---|---|---|
| `SEC_USER_AGENT` | For a new data run | Your name and contact email; required for SEC requests |
| `OPENAI_API_KEY` | For AI features | Azure OpenAI-compatible API key |
| `LLM_MODEL` | No | Deployment/model name; defaults to `gpt-6.1-sol` |
| `FINNHUB_API_KEY` | No | Uses Finnhub company news when set; otherwise Yahoo news |

Endpoint and API version are configured in `config.py`:

- Endpoint: `https://app-uoaihack2ix6ak.azurewebsites.net`
- API version: `2024-12-01-preview`
- Client: `openai.AzureOpenAI`

The API endpoint, model, universe, source endpoints, limits and application defaults
live in `config.py`. Never put real credentials in source files.

A missing AI key leaves the quantitative pipeline working with zero AI conviction
adjustments. The app explicitly labels unavailable commentary and memo features.

A missing SEC user agent blocks new fetches with a clear error. It does not
prevent viewing a previously saved run offline.

## Run

```bash
streamlit run app.py
```

1. Review the client settings and ticker universe.
2. Select **Run analysis**.
3. Inspect Recommendation, Deep dive, Screened out and Method.
4. Download the full research JSON for audit.

**Run analysis** reuses ticker records younger than 24 hours but recomputes AI
analysis and the memo. **Refresh data** bypasses the ticker cache and runs the
complete pipeline.

Changing sidebar settings does not silently change the saved recommendation.
A new run must complete first.

## Architecture

| File | Responsibility |
|---|---|
| `config.py` | Environment configuration, source URLs and defaults |
| `data_sources.py` | Concurrent Yahoo/SEC/Finnhub fetching, annual extraction, provenance |
| `scoring.py` | Pure metrics, pillar scores and deterministic scenarios |
| `ai_analyst.py` | Facts, cited JSON analysis, schema validation, memo, number check |
| `allocation.py` | Eligibility, final scores, water-filling caps, whole shares |
| `storage.py` | Atomic JSON storage |
| `app.py` | Streamlit UI and orchestration |
| `tests/` | Offline unit tests |

Ticker fetches and analyst calls use at most five workers each. SEC requests are
globally paced at least 0.16 seconds apart within the Python process, below ten
requests per second. Do not deploy several independent workers behind the same IP
without a shared SEC rate limiter.

Network requests have timeouts. SEC throttling/service-unavailable responses are
retried once. Each ticker and each source can fail independently. Quote, news,
fundamental and release errors are retained alongside partial results.

AI JSON failures trigger one retry, then an explicit unavailable analysis and a
zero conviction adjustment. Memo generation uses one logical model call.

## Data and auditability

Each fetched field is stored as a value with a provider and public source URL.
Annual observations additionally retain fiscal end, filing date, XBRL tag, unit,
company-facts URL and filing URL. The ticker and fetch timestamp are metadata.

The last five available fiscal-end years are selected independently for each
financial series. Annual duration observations must be 330–380 days long.
Comparative observations use their actual period end rather than the filing's
`fy` label. Latest-filed restatements win within a tag. Ordered standard-tag
fallbacks fill gaps without blindly summing overlapping revenue concepts.

Only `10-K`/`FY` US-GAAP observations are used. Foreign 20-F/IFRS data and custom
tags are not synthesized. Long-term debt is not total debt, and the noncurrent
fallback can omit current maturities.

The newest earnings item-2.02 8-K in the SEC recent-submissions window is checked.
The filing's archive JSON and document index are used to identify EX-99.1.
HTML is reduced to text and truncated to 12,000 characters. Unsupported or
unidentifiable exhibits stay missing rather than being replaced by an unrelated
filing. Older submissions archive files are not exhaustively searched.

The Deep dive source ledger exposes the exact F/N/E facts sent to the analyst.
Citation links target entries inside that ledger; expand it before following a
citation. Quotes, including the ETF quote, are also linked from Recommendation.

Computed values are traceable to raw inputs and the formulas in Method, but are
not themselves reported company figures. AI judgments are labeled interpretations.

## Allocation conventions

- Core defaults to 30% in VOO; VT and QQQ are alternatives.
- Default satellite target is eight stocks.
- Stock/sector defaults are 15%/35% of the original portfolio amount.
- The ETF is exempt from stock and sector caps; no ETF sector look-through.
- Missing inputs receive zero score contributions and reduce data confidence.
- Negative/zero EPS endpoints do not produce a CAGR or PEG.
- Annual EPS is used for scenarios, alongside Yahoo trailing P/E and quote data.
- A nonnegative modeled bear return produces no usable asymmetry ratio.
- Stock eligibility requires asymmetry of at least 1.5 and a positive price/score.
- Final score adds AI adjustment and `min(10, max(0, 2 × asymmetry))`.
- Ties resolve alphabetically.
- Score-weighted water filling redistributes excess within the selected top N.
- Infeasible capacity and whole-share rounding remain cash.
- Weighted recommendation KPIs exclude the ETF and cash.
- No prices, fundamentals or successful AI responses are fabricated.

The model can legitimately recommend fewer than six stocks if public data or
constraints do not support more. The app explains this rather than weakening
the client's rules without disclosure.

## Memo number check

The exact allocation and analysis facts sent to the memo model are retained.
A regex extracts signed, decimal, comma-separated, dollar and percentage figures.
Each figure must match a supplied numeric value within half a unit of its displayed
precision. Percentages are supplied explicitly in percentage-point units.

The check reports unknown figures in amber. A green badge means numeric
occurrence/rounding matched—not that units, statements, citation entailment,
investment suitability or the entire memo have been independently verified.
Numbers embedded in fact IDs are not counted.

## Tests

No keys or network requests are needed for the tests. Install requirements first.

```bash
python -m unittest discover -s tests -v
```

Coverage includes CAGR, PEG, missing-data scoring, annual alignment, scenario
growth/multiple caps, source-period selection, schema citation validation, memo
rounding, stock/sector redistribution, infeasible capacity and dollar conservation.

## Persistence and privacy

- Ticker records: `data/cache/{TICKER}.json`
- Last complete run: `data/last_run.json`
- Both use atomic file replacement.
- A completed run is also retained in `st.session_state`.
- On server restart, the last complete run loads without network calls.
- Keys are not saved in research files; generated data is excluded from Git.

The last-run file is local and shared by users of the same deployment. This is a
single-client demo, not a multi-tenant portfolio service. Do not use it to store
sensitive client information on a shared deployment.

Selected public financial facts, headlines and earnings excerpts are sent to the
configured LLM endpoint. The memo call sends the computed allocation and selected
AI analyses. Review your endpoint's data policy before production use.

## Demo tips

1. Set the SEC user agent and AI key before starting Streamlit.
2. Test that the configured model deployment is accessible to your key.
3. **Run once before presenting so the cache is warm and the complete run is saved.**
4. Restart the app once and confirm the saved recommendation appears without a
   network call. Review its data timestamp before presenting.
5. Keep the successful `data/last_run.json` locally. Viewing it does not require
   keys or internet.
6. For the eight-minute presentation, start with the allocation and signature
   asymmetry KPI, inspect a funded stock, show its SEC citation and scenario chart,
   then finish with Method and the memo check.
7. Avoid pressing Refresh during the pitch unless live fetching is the point:
   public APIs can throttle, return incomplete coverage or change schemas.
8. Never replace failed observations with made-up prices or fundamentals.

A cold run may take several minutes depending on public sources and model
latency. Cached financial data removes most public fetching, but clicking Run
still reruns the AI. Simply opening the saved run is the instant demo path.

## Troubleshooting

- **SEC 403/429:** verify the descriptive user agent; wait before refreshing.
  Some hosted/cloud IPs are blocked by SEC. Do not bypass access controls.
- **Yahoo missing quotes:** wait and refresh later; public quote access is not
  guaranteed. The failure is recorded without breaking other tickers.
- **AI unavailable:** check the key, deployment name, endpoint access and support
  for JSON response format. The configured model name must exist at the endpoint.
- **Foreign issuer excluded:** inspect its missing US-GAAP 10-K history.
- **Large cash balance:** inspect caps, whole-share prices, sector concentration
  and eligibility; this is often a valid result rather than an allocation error.
- **Cannot persist:** ensure the process can create and write the `data` directory.
