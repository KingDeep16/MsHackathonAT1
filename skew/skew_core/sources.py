"""Fetch public data for one ticker: SEC EDGAR fundamentals and filings, Nasdaq prices, news headlines.

Every value is returned with the URL it came from and when it was fetched,
so anything shown in the app can be traced back to its source.
"""

import html
import re
import threading
import time
from datetime import date, datetime, timezone

import httpx

from . import config

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
SEC_ARCHIVE_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}"
NASDAQ_INFO_URL = "https://api.nasdaq.com/api/quote/{ticker}/info"
NASDAQ_HISTORY_URL = "https://api.nasdaq.com/api/quote/{ticker}/historical"
GOOGLE_NEWS_URL = "https://news.google.com/rss/search"
FINNHUB_NEWS_URL = "https://finnhub.io/api/v1/company-news"

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}

# Concepts tried in order; the one with the most recent annual data wins
CONCEPTS = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues",
                "SalesRevenueNet", "RevenueFromContractWithCustomerIncludingAssessedTax"],
    "eps": ["EarningsPerShareDiluted", "EarningsPerShareBasicAndDiluted"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
    "long_term_debt": ["LongTermDebtNoncurrent", "LongTermDebt"],
    "diluted_shares": ["WeightedAverageNumberOfDilutedSharesOutstanding"],
    "equity": ["StockholdersEquity",
               "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"],
}
# Balance-sheet items are point-in-time; the rest cover a full fiscal year
INSTANT = {"long_term_debt", "equity"}

# Broad sector buckets from SEC SIC codes, used for the sector cap
SIC_SECTORS = [
    ((2830, 2836), "Healthcare"), ((3841, 3851), "Healthcare"), ((8000, 8099), "Healthcare"),
    ((6324, 6324), "Healthcare"), ((5122, 5122), "Healthcare"),
    ((3570, 3579), "Technology"), ((3600, 3699), "Technology"), ((3670, 3679), "Technology"),
    ((7370, 7379), "Technology"), ((3661, 3669), "Technology"),
    ((4800, 4899), "Communication"), ((2710, 2741), "Communication"), ((7810, 7849), "Communication"),
    ((6000, 6299), "Financials"), ((6300, 6411), "Financials"), ((6500, 6799), "Financials"),
    ((7389, 7389), "Financials"), ((7320, 7329), "Financials"),
    ((5200, 5999), "Consumer"), ((2000, 2399), "Consumer"), ((7000, 7099), "Consumer"),
    ((4700, 4799), "Consumer"), ((5800, 5899), "Consumer"),
    ((1300, 1399), "Energy"), ((2900, 2999), "Energy"), ((4900, 4999), "Utilities"),
    ((3700, 3799), "Industrials"), ((3400, 3569), "Industrials"), ((4000, 4599), "Industrials"),
]

_cache = {}
_cache_lock = threading.Lock()
CACHE_SECONDS = 6 * 3600


class DataError(Exception):
    pass


def _cached(key, fetch):
    with _cache_lock:
        hit = _cache.get(key)
        if hit and time.time() - hit[0] < CACHE_SECONDS:
            return hit[1]
    value = fetch()
    with _cache_lock:
        _cache[key] = (time.time(), value)
    return value


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_sec_lock = threading.Lock()
_sec_last = [0.0]
SEC_MIN_INTERVAL = 1 / 8  # SEC fair-use limit is 10 requests/second


def _sec_get(client, url):
    if not config.SEC_USER_AGENT:
        raise DataError("SEC_USER_AGENT is not set (the SEC requires a name and email, e.g. 'Jane Doe jane@example.com').")
    with _sec_lock:
        wait = _sec_last[0] + SEC_MIN_INTERVAL - time.time()
        if wait > 0:
            time.sleep(wait)
        _sec_last[0] = time.time()
    response = client.get(url, headers={"User-Agent": config.SEC_USER_AGENT})
    response.raise_for_status()
    return response


def sector_from_sic(sic):
    try:
        code = int(sic)
    except (TypeError, ValueError):
        return "Other"
    for (low, high), sector in SIC_SECTORS:
        if low <= code <= high:
            return sector
    return "Other"


def lookup_cik(client, ticker):
    tickers = _cached("sec_tickers", lambda: _sec_get(client, SEC_TICKERS_URL).json())
    symbol = ticker.upper().replace(".", "-")
    for row in tickers.values():
        if row["ticker"].upper() == symbol:
            return int(row["cik_str"]), row["title"]
    raise DataError(f"{ticker} was not found in SEC EDGAR (only US-listed 10-K filers are supported).")


def _annual_series(gaap, concept, instant):
    """Return [(fiscal_year_end, value)] from 10-K filings, one per year, latest filing wins."""
    units = gaap.get(concept, {}).get("units", {})
    rows = units.get("USD") or units.get("USD/shares") or units.get("shares") or []
    by_end = {}
    for row in rows:
        if row.get("form") not in ("10-K", "10-K/A") or row.get("fp") != "FY":
            continue
        if not instant:
            if "start" not in row:
                continue
            days = (date.fromisoformat(row["end"]) - date.fromisoformat(row["start"])).days
            if not 350 <= days <= 380:
                continue
        previous = by_end.get(row["end"])
        if previous is None or row["filed"] > previous["filed"]:
            by_end[row["end"]] = row
    return sorted((end, row["val"]) for end, row in by_end.items())


def _split_factor(ratio):
    """A share-count jump of 1.5x-60x is a stock split (or reverse split), not issuance.
    Bigger jumps are unit errors in the filing data (e.g. shares reported in millions), not splits."""
    if 1.5 < ratio <= 60:
        return round(ratio)
    if 1 / 60 <= ratio < 1 / 1.5:
        return 1 / round(1 / ratio)
    return 1


def split_adjust_eps(eps, diluted_shares, shares_outstanding_now=None):
    """Restate EPS on today's share basis. SEC data mixes pre- and post-split figures across filings."""
    shares = dict(diluted_shares)
    adjusted = [list(row) for row in eps]
    years = [d for d, _ in diluted_shares]
    for earlier, later in zip(years, years[1:]):
        if not shares[earlier] or not shares[later]:
            continue
        factor = _split_factor(shares[later] / shares[earlier])
        if factor != 1:
            for row in adjusted:
                if row[0] <= earlier:
                    row[1] /= factor
    # A split after the latest annual report shows up in the latest cover-page share count
    if shares_outstanding_now and diluted_shares and diluted_shares[-1][1]:
        factor = _split_factor(shares_outstanding_now / diluted_shares[-1][1])
        if factor != 1:
            for row in adjusted:
                row[1] /= factor
    return [tuple(row) for row in adjusted]


def _latest_shares_outstanding(companyfacts, after):
    """Cover-page share count, only if it is more recent than the latest annual report (some are years stale)."""
    rows = companyfacts.get("facts", {}).get("dei", {}).get("EntityCommonStockSharesOutstanding", {}) \
        .get("units", {}).get("shares", [])
    if not rows:
        return None
    latest_end = max(r["end"] for r in rows)
    if not after or latest_end <= after:
        return None
    # Companies with several share classes report one row per class on the same date
    return sum(r["val"] for r in rows if r["end"] == latest_end and r.get("form", "").startswith("10-"))


def extract_financials(companyfacts, years=5):
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    financials = {}
    for field, concepts in CONCEPTS.items():
        best = []
        for concept in concepts:
            series = _annual_series(gaap, concept, field in INSTANT)
            if series and (not best or series[-1][0] > best[-1][0]):
                best = series
        financials[field] = best[-(years + 1):]
    if not financials["eps"] and financials["net_income"] and financials["diluted_shares"]:
        shares = dict(financials["diluted_shares"])
        financials["eps"] = [(d, ni / shares[d]) for d, ni in financials["net_income"] if shares.get(d)]
    latest_year = financials["diluted_shares"][-1][0] if financials["diluted_shares"] else None
    financials["eps"] = split_adjust_eps(financials["eps"], financials["diluted_shares"],
                                         _latest_shares_outstanding(companyfacts, latest_year))
    return financials


def _strip_html(text):
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def latest_earnings_release(client, cik, submissions, max_chars=12000):
    recent = submissions.get("filings", {}).get("recent", {})
    for i, form in enumerate(recent.get("form", [])):
        if form != "8-K" or "2.02" not in recent["items"][i]:
            continue
        accession = recent["accessionNumber"][i]
        base = SEC_ARCHIVE_URL.format(cik=cik, acc=accession.replace("-", ""))
        index_html = _sec_get(client, f"{base}/{accession}-index.html").text
        exhibit = None
        for row in re.findall(r"(?is)<tr.*?</tr>", index_html):
            link = re.search(r'href="([^"]+\.(?:htm|html|txt))"', row, re.I)
            if link and re.search(r">\s*EX-99(?:\.0?1)?\s*<", row):
                exhibit = link.group(1).split("/")[-1]
                if re.search(r">\s*EX-99\.0?1\s*<", row):
                    break
        if not exhibit:
            return None
        url = f"{base}/{exhibit}"
        return {
            "filed": recent["filingDate"][i],
            "url": url,
            "text": _strip_html(_sec_get(client, url).text)[:max_chars],
        }
    return None


def _money(text):
    try:
        return float(str(text).replace("$", "").replace(",", ""))
    except ValueError:
        return None


def nasdaq_price(client, ticker):
    symbol = ticker.upper().replace("-", ".")
    for asset_class in ("stocks", "etf"):
        info = client.get(NASDAQ_INFO_URL.format(ticker=symbol), params={"assetclass": asset_class},
                          headers=BROWSER_HEADERS).json()
        if info.get("data"):
            break
    else:
        raise DataError(f"Nasdaq has no quote for {ticker}.")
    price = _money(info["data"]["primaryData"]["lastSalePrice"])

    today = date.today()
    params = {"assetclass": asset_class, "fromdate": today.replace(year=today.year - 1).isoformat(),
              "todate": today.isoformat(), "limit": 400}
    history = client.get(NASDAQ_HISTORY_URL.format(ticker=symbol), params=params, headers=BROWSER_HEADERS).json()
    rows = ((history.get("data") or {}).get("tradesTable") or {}).get("rows") or []
    closes = [c for c in (_money(r["close"]) for r in reversed(rows)) if c]
    step = max(1, len(closes) // 52)  # ~weekly points for the sparkline
    return {
        "price": price,
        "high_52w": max(closes) if closes else None,
        "low_52w": min(closes) if closes else None,
        "history": closes[::step] + ([closes[-1]] if closes and (len(closes) - 1) % step else []),
        "as_of": info["data"]["primaryData"].get("lastTradeTimestamp"),
        "url": f"https://www.nasdaq.com/market-activity/{'etf' if asset_class == 'etf' else 'stocks'}/{symbol.lower()}",
    }


def news(client, ticker, limit=8):
    if config.FINNHUB_API_KEY:
        today = date.today()
        params = {"symbol": ticker.upper(), "from": date.fromordinal(today.toordinal() - 30).isoformat(),
                  "to": today.isoformat(), "token": config.FINNHUB_API_KEY}
        rows = client.get(FINNHUB_NEWS_URL, params=params).json()[:limit]
        return [{"title": r["headline"], "publisher": r.get("source", "Finnhub"), "url": r["url"],
                 "date": datetime.fromtimestamp(r["datetime"], timezone.utc).date().isoformat()} for r in rows]
    query = f'"{ticker.upper()}" stock when:21d'
    params = {"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"}
    feed = client.get(GOOGLE_NEWS_URL, params=params, headers={"User-Agent": BROWSER_HEADERS["User-Agent"]}).text
    headlines = []
    for item in re.findall(r"(?s)<item>(.*?)</item>", feed)[:limit]:
        def tag(name):
            match = re.search(rf"(?s)<{name}[^>]*>(.*?)</{name}>", item)
            return html.unescape(match.group(1)).strip() if match else ""
        publisher = tag("source") or "Google News"
        title = tag("title")
        if title.endswith(f" - {publisher}"):
            title = title[: -len(publisher) - 3]
        published = tag("pubDate")
        try:
            published = datetime.strptime(published, "%a, %d %b %Y %H:%M:%S %Z").date().isoformat()
        except ValueError:
            pass
        headlines.append({"title": title, "publisher": publisher, "url": tag("link"), "date": published})
    return headlines


def fetch_company(ticker):
    """Everything we know about one ticker. Partial failures are recorded in `warnings`, not raised."""
    ticker = ticker.strip().upper()
    warnings = []
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        cik, title = lookup_cik(client, ticker)
        facts_url = SEC_FACTS_URL.format(cik=cik)
        submissions_url = SEC_SUBMISSIONS_URL.format(cik=cik)
        companyfacts = _sec_get(client, facts_url).json()
        submissions = _sec_get(client, submissions_url).json()

        try:
            price = nasdaq_price(client, ticker)
        except Exception as error:
            price = None
            warnings.append(f"Price unavailable: {error}")
        try:
            release = latest_earnings_release(client, cik, submissions)
            if release is None:
                warnings.append("No recent earnings release (8-K item 2.02) found.")
        except Exception as error:
            release = None
            warnings.append(f"Earnings release unavailable: {error}")
        try:
            headlines = news(client, ticker)
        except Exception as error:
            headlines = []
            warnings.append(f"News unavailable: {error}")

    return {
        "ticker": ticker,
        "name": submissions.get("name") or title,
        "sector": sector_from_sic(submissions.get("sic")),
        "industry": submissions.get("sicDescription") or "",
        "cik": cik,
        "fetched_at": _now(),
        "financials": extract_financials(companyfacts),
        "price": price,
        "earnings_release": release,
        "news": headlines,
        "sources": {
            "financials": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=10-K",
            "financials_api": facts_url,
            "price": price["url"] if price else None,
        },
        "warnings": warnings,
    }


def fetch_etf_price(ticker):
    return _cached(f"etf:{ticker}", lambda: _fetch_etf_price(ticker))


def _fetch_etf_price(ticker):
    with httpx.Client(timeout=20, follow_redirects=True) as client:
        return nasdaq_price(client, ticker)
