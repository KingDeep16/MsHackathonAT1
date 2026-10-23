"""Build Skew's static dataset for the S&P 500.

Combines the Stocks-Research daily snapshot (price, analyst consensus, earnings record, insider and
Congress activity, news) with SEC EDGAR fundamentals and each company's latest earnings release.
The source repo is only read, never modified.

Usage: SEC_USER_AGENT="Name email" python scripts/build_dataset.py /path/to/Stocks-Research
"""

import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from skew_core import metrics, sources  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "public" / "data"

# The only tickers with hand-researched moat/catalyst/risk notes in the source project;
# everything else there is a generic default, so it is left out rather than shown as research
CURATED = {"NVDA", "AAPL", "GRAB", "PLTR", "ASTS", "VKTX", "RKLB", "CRWD", "MSFT", "AMZN", "TSLA"}

# SEC Form 4 transaction codes
TX_CODES = {"P": "Open-market buy", "S": "Open-market sale", "F": "Shares withheld for tax",
            "A": "Award / grant", "M": "Option exercise", "G": "Gift", "D": "Disposition to issuer",
            "C": "Conversion", "X": "Option exercise"}


def source_file(repo, symbol):
    return repo / "data" / "stocks" / f"{symbol.replace('/', '_')}.json"


def slim_reference(ref):
    earnings = ref.get("earnings") or {}
    history = ref.get("history_close") or []
    insiders = []
    for tx in (ref.get("insider_transactions") or [])[:10]:
        insiders.append({
            "date": tx.get("date"), "insider": tx.get("insider"), "position": tx.get("position"),
            "type": TX_CODES.get(tx.get("tx_code"), tx.get("type")), "code": tx.get("tx_code"),
            "shares": tx.get("shares"), "value": tx.get("value"), "url": tx.get("doc_url"),
        })
    congress = ref.get("congress_summary") or {}
    news = [{"title": n.get("title"), "publisher": n.get("provider") or n.get("source"), "url": n.get("url"),
             "date": n.get("date") or n.get("published")}
            for n in (ref.get("news") or []) if n.get("title") and "Calendar" not in str(n.get("source"))][:8]
    out = {
        "summary": (ref.get("summary") or "")[:900],
        "ceo": ref.get("ceo_name"),
        "market_cap": ref.get("market_cap"),
        "pe_forward": ref.get("pe_forward"),
        "gross_margin": ref.get("gross_margin"),
        "roe": ref.get("roe"),
        "beta": ref.get("beta"),
        "high_52w": ref.get("fifty_two_week_high"),
        "low_52w": ref.get("fifty_two_week_low"),
        "analyst": {
            "mean": ref.get("target_mean"), "high": ref.get("target_high"), "low": ref.get("target_low"),
            "rating": ref.get("analyst_rating"), "count": ref.get("num_analysts"),
            "upside_pct": ref.get("upside_to_mean"),
        },
        "earnings_record": earnings.get("track_record"),
        "earnings_history": (earnings.get("history") or [])[:8],
        "next_earnings": earnings.get("upcoming"),
        "insiders": insiders,
        "congress": {k: congress.get(k) for k in ("total_trades", "buys", "sells", "sentiment")} if congress else None,
        "technicals": {"rsi": ref.get("rsi"), "sma200": ref.get("sma200"), "trend_bullish": ref.get("trend_bullish")},
        "news": news,
        "history": {"dates": (ref.get("history_dates") or [])[-60:], "close": history[-60:]},
    }
    if ref.get("symbol") in CURATED:
        out["curated"] = {"moat": ref.get("economic_moat"), "moat_type": ref.get("moat_type"),
                          "catalysts": ref.get("catalysts"), "risks": ref.get("risks")}
    return out


def build_one(repo, row):
    symbol = row["symbol"]
    ref = json.loads(source_file(repo, symbol).read_text())
    price = ref.get("price") or row.get("price")
    with httpx.Client(timeout=25, follow_redirects=True) as client:
        cik, _ = sources.lookup_cik(client, symbol)
        facts = sources._sec_get(client, sources.SEC_FACTS_URL.format(cik=cik)).json()
        submissions = sources._sec_get(client, sources.SEC_SUBMISSIONS_URL.format(cik=cik)).json()
        try:
            release = sources.latest_earnings_release(client, cik, submissions)
        except Exception:
            release = None

    company = {
        "ticker": symbol, "name": row["name"], "sector": row["sector"], "industry": row.get("sub_industry", ""),
        "cik": cik, "financials": sources.extract_financials(facts),
        "price": {"price": price, "url": f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}"},
        "earnings_release": release,
        "sources": {"financials": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K",
                    "price": f"https://www.nasdaq.com/market-activity/stocks/{symbol.lower()}"},
    }
    analysis = metrics.analyse(company)
    detail = {**company, **analysis, "reference": slim_reference(ref)}
    return detail


def summary_row(d):
    m, s, ref = d["metrics"], d["scenarios"], d["reference"]
    closes = ref["history"]["close"]
    step = max(1, len(closes) // 30)
    return {
        "ticker": d["ticker"], "name": d["name"], "sector": d["sector"], "industry": d["industry"], "cik": d["cik"],
        "price": m["price"], "market_cap": ref.get("market_cap"),
        "pe": m["pe"], "peg": m["peg"], "revenue_cagr_3y": m["revenue_cagr_3y"], "eps_cagr_3y": m["eps_cagr_3y"],
        "operating_margin": m["operating_margin"], "fcf_margin": m["fcf_margin"],
        "score": d["score"]["total"], "pillars": d["score"]["pillars"], "confidence": d["score"]["confidence"],
        "asymmetry": s["asymmetry"] if s else None,
        "upside": s["upside"] if s else None, "downside": s["downside"] if s else None,
        "analyst_upside_pct": ref["analyst"]["upside_pct"], "analyst_rating": ref["analyst"]["rating"],
        "beat_rate_pct": (ref.get("earnings_record") or {}).get("beat_rate_pct"),
        "has_release": bool(d.get("earnings_release")),
        "spark": closes[::step],
    }


def main():
    repo = Path(sys.argv[1]).resolve()
    universe = [r for r in json.loads((repo / "sp500_universe.json").read_text()) if r.get("is_sp500")]
    (OUT / "stocks").mkdir(parents=True, exist_ok=True)

    rows, failed = [], []
    started = time.time()

    def task(row):
        try:
            return build_one(repo, row), None
        except Exception as error:
            return None, (row["symbol"], str(error)[:160])

    # 5 workers x ~3 SEC requests each stays under the SEC's 10 requests/second limit
    with ThreadPoolExecutor(5) as pool:
        for i, (detail, error) in enumerate(pool.map(task, universe), start=1):
            if error:
                failed.append(error)
            else:
                (OUT / "stocks" / f"{detail['ticker']}.json").write_text(json.dumps(detail, separators=(",", ":")))
                rows.append(summary_row(detail))
            if i % 25 == 0:
                print(f"{i}/{len(universe)} done, {len(failed)} failed, {time.time() - started:.0f}s", flush=True)

    # One row per company: share classes (GOOG/GOOGL, FOX/FOXA, NWS/NWSA) share a CIK; keep class A
    by_cik = {}
    for r in sorted(rows, key=lambda r: r["ticker"]):
        by_cik[r.pop("cik")] = r
    rows = sorted(by_cik.values(), key=lambda r: r["ticker"])
    (OUT / "universe.json").write_text(json.dumps({
        "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe": "S&P 500",
        "count": len(rows),
        "failed": failed,
        "stocks": rows,
    }, separators=(",", ":")))
    print(f"Wrote {len(rows)} stocks, {len(failed)} failed in {time.time() - started:.0f}s")
    for symbol, error in failed:
        print(f"  {symbol}: {error}")


if __name__ == "__main__":
    main()
