import math
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin

import requests
import yfinance as yf

import config
from storage import read_json, write_json

_SEC_LOCK = threading.Lock()
_SEC_LAST_REQUEST = 0.0


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def tagged(value, source, provider, **metadata):
    return {"value": value, "source": source, "provider": provider, **metadata}


def numeric(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def sec_get(url):
    global _SEC_LAST_REQUEST
    if not config.SEC_USER_AGENT.strip():
        raise ValueError(
            "SEC_USER_AGENT is required. Set it to your name and email address."
        )
    for attempt in range(2):
        with _SEC_LOCK:
            wait = config.SEC_REQUEST_INTERVAL - (time.monotonic() - _SEC_LAST_REQUEST)
            if wait > 0:
                time.sleep(wait)
            _SEC_LAST_REQUEST = time.monotonic()
        response = requests.get(
            url,
            headers={
                "User-Agent": config.SEC_USER_AGENT,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=config.HTTP_TIMEOUT,
        )
        if response.status_code in (429, 503) and attempt == 0:
            time.sleep(2)
            continue
        response.raise_for_status()
        return response
    raise RuntimeError("SEC request failed")


class TextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self.hidden += 1
        if tag in ("p", "div", "br", "tr", "li", "h1", "h2", "h3"):
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)

    def text(self):
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


class ExhibitIndexParser(HTMLParser):
    """Read filing document rows to identify the actual EX-99.1 document."""
    def __init__(self):
        super().__init__()
        self.in_row = False
        self.row_text = []
        self.row_links = []
        self.matches = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.in_row = True
            self.row_text = []
            self.row_links = []
        if tag == "a" and self.in_row:
            href = dict(attrs).get("href")
            if href:
                self.row_links.append(href)

    def handle_data(self, data):
        if self.in_row:
            self.row_text.append(data)

    def handle_endtag(self, tag):
        if tag == "tr":
            if re.search(r"\bEX-99\.1\b", " ".join(self.row_text), re.I):
                self.matches.extend(self.row_links)
            self.in_row = False


def extract_annual(companyfacts, cik):
    """Select annual durations, not comparative values relabeled by filing FY.

    An observation's fiscal year comes from its fiscal end year. Latest-filed
    restatements win within a tag. Tag priority is used only for overlapping years.
    """
    gaap = companyfacts.get("facts", {}).get("us-gaap", {})
    result = {}
    base_source = f"{config.SEC_FACTS_BASE}/CIK{int(cik):010d}.json"
    for metric, tag_names in config.FINANCIAL_TAGS.items():
        by_year = {}
        for tag_name in tag_names:
            unit = "USD/shares" if metric == "eps" else "USD"
            observations = gaap.get(tag_name, {}).get("units", {}).get(unit, [])
            candidates = {}
            for observation in observations:
                if observation.get("form") != "10-K" or observation.get("fp") != "FY":
                    continue
                value = numeric(observation.get("val"))
                if value is None:
                    continue
                try:
                    end = datetime.fromisoformat(observation["end"]).date()
                    if metric not in ("debt", "equity"):
                        start = datetime.fromisoformat(observation["start"]).date()
                        if not 330 <= (end - start).days <= 380:
                            continue
                except (ValueError, KeyError):
                    continue
                year = end.year
                rank = (observation.get("filed", ""), observation.get("accn", ""))
                if year not in candidates or rank > candidates[year][0]:
                    accession = observation.get("accn", "")
                    filing_url = (
                        f"{config.SEC_ARCHIVES_BASE}/{int(cik)}/"
                        f"{accession.replace('-', '')}/{accession}-index.html"
                        if accession else base_source
                    )
                    candidates[year] = (
                        rank,
                        tagged(
                            value, filing_url, "SEC EDGAR",
                            year=year, end=end.isoformat(), tag=tag_name,
                            filed=observation.get("filed"), unit=unit,
                            facts_url=base_source,
                        ),
                    )
            for year, (_, observation) in candidates.items():
                if year not in by_year:
                    by_year[year] = observation
        result[metric] = tagged(
            [by_year[year] for year in sorted(by_year)[-5:]],
            base_source, "SEC EDGAR",
        )
    return result


def latest_release(cik):
    submission_url = f"{config.SEC_SUBMISSIONS_BASE}/CIK{int(cik):010d}.json"
    submissions = sec_get(submission_url).json()
    recent = submissions.get("filings", {}).get("recent", {})
    candidates = []
    for index, form in enumerate(recent.get("form", [])):
        items = recent.get("items", [])
        if form == "8-K" and index < len(items) and "2.02" in items[index]:
            candidates.append((
                recent["filingDate"][index],
                recent["accessionNumber"][index],
            ))
    if not candidates:
        return tagged(
            None, submission_url, "SEC EDGAR",
            note="No earnings 8-K item 2.02 in the recent submissions window.",
        )
    date, accession = max(candidates)
    directory = f"{config.SEC_ARCHIVES_BASE}/{int(cik)}/{accession.replace('-', '')}/"
    listing = sec_get(directory + "index.json").json()
    names = [
        entry["name"]
        for entry in listing.get("directory", {}).get("item", [])
        if entry.get("name", "").lower().endswith((".htm", ".html", ".txt"))
    ]
    filing_url = directory + accession + "-index.html"
    parser = ExhibitIndexParser()
    parser.feed(sec_get(filing_url).text)
    exhibit_url = None
    for link in parser.matches:
        resolved = urljoin("https://www.sec.gov", link)
        if "/ixviewer/" in resolved or "/ix?doc=" in resolved:
            document = resolved.split("doc=", 1)[-1]
            resolved = urljoin("https://www.sec.gov", document)
        if resolved.startswith(directory):
            exhibit_url = resolved
            break
    if exhibit_url is None:
        plausible = [
            name for name in names
            if re.search(r"(?:ex(?:hibit)?[-_]?)?99[-_.]?1(?:\D|$)", name, re.I)
            and "index" not in name.lower()
        ]
        if len(plausible) == 1:
            exhibit_url = directory + plausible[0]
    if not exhibit_url:
        return tagged(
            None, filing_url, "SEC EDGAR", filing_date=date,
            note="Latest earnings 8-K found, but EX-99.1 could not be identified.",
        )
    extractor = TextExtractor()
    extractor.feed(sec_get(exhibit_url).text)
    text = extractor.text()[:config.RELEASE_LIMIT]
    return tagged(
        text or None, filing_url, "SEC EDGAR",
        exhibit_url=exhibit_url, filing_date=date,
    )


def normalize_news(items, provider, source):
    result = []
    seen = set()
    for item in items:
        if provider == "Finnhub":
            headline = item.get("headline")
            url = item.get("url", "")
            publisher = item.get("source", "Unknown")
            date = item.get("datetime")
        else:
            content = item.get("content") or item
            headline = content.get("title")
            url_object = content.get("canonicalUrl") or content.get("clickThroughUrl") or {}
            url = url_object.get("url", "") if isinstance(url_object, dict) else ""
            url = url or content.get("link", "")
            publisher_object = content.get("provider") or {}
            publisher = (
                publisher_object.get("displayName", "Yahoo Finance")
                if isinstance(publisher_object, dict) else str(publisher_object)
            )
            publisher = content.get("publisher", publisher)
            date = content.get("pubDate", content.get("providerPublishTime"))
        if not headline or headline in seen:
            continue
        seen.add(headline)
        if isinstance(date, (int, float)):
            date = datetime.fromtimestamp(date, timezone.utc).isoformat()
        citation = url if str(url).startswith(("https://", "http://")) else source
        result.append({
            "headline": tagged(str(headline), citation, provider),
            "date": tagged(str(date) if date else None, citation, provider),
            "publisher": tagged(str(publisher), citation, provider),
            "url": tagged(citation, citation, provider),
        })
        if len(result) >= config.NEWS_LIMIT:
            break
    return tagged(result, source, provider)


def fetch_news(ticker, yahoo):
    if config.FINNHUB_API_KEY:
        endpoint = "https://finnhub.io/api/v1/company-news"
        today = datetime.now(timezone.utc).date()
        response = requests.get(
            endpoint,
            params={
                "symbol": ticker,
                "from": (today - timedelta(days=14)).isoformat(),
                "to": today.isoformat(),
                "token": config.FINNHUB_API_KEY,
            },
            timeout=config.HTTP_TIMEOUT,
        )
        response.raise_for_status()
        items = response.json()
        if not isinstance(items, list):
            raise ValueError("Finnhub returned an unexpected news response")
        return normalize_news(
            sorted(items, key=lambda item: item.get("datetime", 0), reverse=True),
            "Finnhub", "https://finnhub.io/docs/api/company-news",
        )
    return normalize_news(
        yahoo.news or [], "Yahoo Finance", f"https://finance.yahoo.com/quote/{ticker}/news/"
    )


def cache_is_fresh(record, is_etf=False):
    if not record or record.get("is_etf") != is_etf or "fields" not in record:
        return False
    try:
        fetched = datetime.fromisoformat(record["fetched_at"])
        age = datetime.now(timezone.utc) - fetched
        return timedelta(0) <= age < timedelta(hours=config.CACHE_HOURS)
    except (ValueError, KeyError, TypeError):
        return False


def get_cached(ticker, is_etf):
    record = read_json(config.CACHE_DIR / f"{ticker}.json")
    return record if cache_is_fresh(record, is_etf) else None


def short_error(service, error):
    status = getattr(getattr(error, "response", None), "status_code", None)
    suffix = f" (HTTP {status})" if status else ""
    return f"{service}: {type(error).__name__}{suffix}. Data unavailable; other sources continue."


def fetch_ticker(ticker, cik, refresh=False, is_etf=False, mapping_error=None):
    if not refresh:
        cached = get_cached(ticker, is_etf)
        if cached:
            return cached
    record = {
        "ticker": ticker, "fetched_at": utc_now(), "is_etf": is_etf,
        "fields": {}, "errors": [],
    }
    fields = record["fields"]
    source = f"https://finance.yahoo.com/quote/{ticker}/"
    yahoo = yf.Ticker(ticker)
    names = {
        "price": "currentPrice", "sector": "sector", "market_cap": "marketCap",
        "trailing_pe": "trailingPE", "forward_pe": "forwardPE",
        "high_52w": "fiftyTwoWeekHigh", "low_52w": "fiftyTwoWeekLow",
    }
    try:
        info = yahoo.get_info()
        for field, key in names.items():
            value = info.get(key)
            if field == "price":
                value = value or info.get("regularMarketPrice")
            if field != "sector":
                value = numeric(value)
            fields[field] = tagged(value, source, "Yahoo Finance / yfinance")
    except Exception as error:
        record["errors"].append(short_error("Yahoo quote", error))
    for field in names:
        fields.setdefault(field, tagged(None, source, "Yahoo Finance / yfinance"))
    if is_etf:
        fields["sector"] = tagged("Index core", source, "Yahoo Finance / yfinance")
    else:
        try:
            fields["news"] = fetch_news(ticker, yahoo)
        except Exception as error:
            record["errors"].append(short_error("News", error))
            fields["news"] = tagged([], source, "News unavailable")
        fields["cik"] = tagged(cik, config.SEC_TICKERS_URL, "SEC EDGAR")
        if cik:
            facts_url = f"{config.SEC_FACTS_BASE}/CIK{int(cik):010d}.json"
            try:
                fields.update(extract_annual(sec_get(facts_url).json(), cik))
            except Exception as error:
                record["errors"].append(short_error("SEC company facts", error))
            try:
                fields["earnings_release"] = latest_release(cik)
            except Exception as error:
                record["errors"].append(short_error("SEC earnings release", error))
        else:
            record["errors"].append(
                mapping_error or "SEC: ticker has no CIK mapping; annual fundamentals unavailable."
            )
        for metric in config.FINANCIAL_TAGS:
            fields.setdefault(metric, tagged([], config.SEC_TICKERS_URL, "SEC EDGAR"))
        fields.setdefault(
            "earnings_release",
            tagged(None, config.SEC_TICKERS_URL, "SEC EDGAR", note="Release unavailable"),
        )
    try:
        write_json(config.CACHE_DIR / f"{ticker}.json", record)
    except OSError:
        record["errors"].append("Cache could not be written; results remain usable in memory.")
    return record


def fetch_universe(tickers, etf, refresh=False, progress=None):
    if not config.SEC_USER_AGENT.strip():
        raise ValueError("Missing SEC_USER_AGENT. Set it to 'Your Name email@example.com'.")
    jobs = list(dict.fromkeys([*tickers, etf]))
    needs_mapping = refresh or any(not get_cached(ticker, False) for ticker in tickers)
    mapping = {}
    mapping_error = None
    if needs_mapping:
        try:
            raw = sec_get(config.SEC_TICKERS_URL).json()
            mapping = {
                row["ticker"].replace(".", "-").upper(): int(row["cik_str"])
                for row in raw.values()
            }
        except Exception as error:
            mapping_error = short_error("SEC ticker mapping", error)
    records = {}
    with ThreadPoolExecutor(max_workers=config.MAX_WORKERS) as executor:
        futures = {
            executor.submit(
                fetch_ticker, ticker, mapping.get(ticker), refresh,
                ticker == etf, mapping_error,
            ): ticker for ticker in jobs
        }
        for completed, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                records[ticker] = future.result()
            except Exception as error:
                records[ticker] = {
                    "ticker": ticker, "fetched_at": utc_now(), "is_etf": ticker == etf,
                    "fields": {}, "errors": [short_error("Ticker fetch", error)],
                }
            if progress:
                progress(completed, len(jobs), ticker)
    return records
