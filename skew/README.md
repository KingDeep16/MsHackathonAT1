# Skew — SOP Terminal + $100K Client Advisor

Microsoft × LSEG Hackathon 2026. Educational project, not financial advice.

- **`/`** — the SOP Due Diligence Terminal (mirrored from Stocks-Research): 9-step research on ~8,600 stocks.
- **`/advisor/`** — the client recommendation: a long-term asymmetric investor has $100,000. Skew screens the S&P 500 on SEC fundamentals, models 5-year bear/base/bull cases, has GPT read each finalist's earnings release and news (every claim cited), allocates a core-satellite portfolio, and writes a client memo whose numbers are checked against the data.

## Run locally

```bash
cd skew
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
export OPENAI_API_KEY=...        # hackathon portal key
.venv/bin/uvicorn app:app --port 8000
```

Open http://localhost:8000 (terminal) or http://localhost:8000/advisor/ (client advisor).

Tests: `.venv/bin/python -m unittest discover -s tests -t .`

## Deploy to Vercel

Import the repo, set **Root Directory** to `skew`, and add the environment variable `OPENAI_API_KEY`. `app.py` is the FastAPI entry point; `public/` is served as static files.

## Data

- `public/data/`, `public/*.json`, `public/index.html` — Stocks-Research snapshot (terminal).
- `public/advisor/data/` — S&P 500 dataset for the advisor, built by
  `SEC_USER_AGENT="Name email" python scripts/build_dataset.py /path/to/Stocks-Research`
  (SEC EDGAR fundamentals + latest 8-K earnings releases, merged with the Stocks-Research snapshot).
- Live prices for the final picks come from Nasdaq's public quote API. No data-provider keys are needed.

## Demo tips

Run the advisor once before presenting: the last run is saved in the browser and reloads instantly, even offline. Hover the citation tags (E1, F3…) in a stock's analysis to show where each claim came from.
