"""LLM analysis grounded in retrieved facts. The model explains numbers; it never produces them."""

import json
import os
import re
import time

from openai import AzureOpenAI

from . import config

_client = None


def client():
    global _client
    if _client is None:
        key = os.getenv("tSXUUlWzUsofEZ8sXFMEp3F80FbnyMUAE9rpAM0dmAa4pEqn")
        if not key:
            raise RuntimeError("OPENAI_API_KEY is not set.")
        _client = AzureOpenAI(api_version=config.API_VERSION, azure_endpoint=config.AZURE_ENDPOINT, api_key=key,
                              timeout=90, max_retries=0)
    return _client


def chat_json(system, user, max_tokens=4000, attempts=2):
    last_error = None
    for attempt in range(attempts):
        try:
            response = client().chat.completions.create(
                model=config.LLM_MODEL,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                response_format={"type": "json_object"},
                max_completion_tokens=max_tokens,
            )
            return json.loads(response.choices[0].message.content)
        except Exception as error:  # retry transient API errors and malformed JSON alike
            last_error = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"AI request failed after {attempts} attempts: {last_error}")


# ---------- formatting shared by facts and the number check ----------

def money(value):
    if value is None:
        return "n/a"
    sign = "-" if value < 0 else ""
    value = abs(value)
    if value >= 1e9:
        return f"{sign}${value / 1e9:,.1f}B"
    if value >= 1e6:
        return f"{sign}${value / 1e6:,.1f}M"
    return f"{sign}${value:,.2f}"


def pct(value, signed=False):
    if value is None:
        return "n/a"
    return f"{value * 100:+.1f}%" if signed else f"{value * 100:.1f}%"


def num(value, suffix=""):
    return "n/a" if value is None else f"{value:,.2f}{suffix}"


def build_facts(detail):
    """Numbered facts the model may cite. Every fact carries its source."""
    m, s, sc = detail["metrics"], detail["scenarios"], detail["score"]
    ref = detail.get("reference") or {}
    sec = detail["sources"]["financials"]
    snapshot = f"https://finance.yahoo.com/quote/{detail['ticker']}/"
    fy = m["fiscal_year_end"]
    rows = [
        ("Current share price", money(m["price"]), detail["sources"]["price"]),
        (f"Diluted EPS, fiscal year ended {fy}", num(m["eps"]), sec),
        ("P/E ratio (price ÷ latest annual diluted EPS)", num(m["pe"], "x"), "computed"),
        (f"Revenue, fiscal year ended {fy}", money(m["revenue_latest"]), sec),
        ("Revenue growth, 3-year CAGR", pct(m["revenue_cagr_3y"]), sec),
        ("EPS growth, 3-year CAGR", pct(m["eps_cagr_3y"]), sec),
        ("Operating margin, latest year", pct(m["operating_margin"]), sec),
        ("Operating margin change over 3 years (percentage points)",
         "n/a" if m["operating_margin_change_3y"] is None else f"{m['operating_margin_change_3y'] * 100:+.1f}pp", sec),
        ("Free-cash-flow margin, latest year", pct(m["fcf_margin"]), sec),
        ("Long-term debt to equity", "negative equity" if m["negative_equity"] else num(m["debt_to_equity"], "x"), sec),
        ("PEG ratio (P/E ÷ EPS growth %)", num(m["peg"]), "computed"),
        ("Skew quality score (0-100)", f"{sc['total']:.1f}", "computed"),
    ]
    if s:
        for case in ("bear", "base", "bull"):
            c = s["cases"][case]
            rows.append((f"{case.title()} case {s['years']}-year price (EPS growth {pct(c['growth'])}/yr, exit P/E {c['exit_pe']:.1f}x)",
                         f"{money(c['price'])} ({pct(c['return'], signed=True)})", "computed"))
        rows.append(("Asymmetry ratio (bull upside ÷ bear downside)", f"{s['asymmetry']:.1f}x", "computed"))

    analyst = ref.get("analyst") or {}
    if analyst.get("mean"):
        rows.append(("Wall Street 12-month price target (mean / low / high)",
                     f"{money(analyst['mean'])} / {money(analyst.get('low'))} / {money(analyst.get('high'))}", snapshot))
        rows.append(("Analyst consensus", f"{analyst.get('rating')} ({analyst.get('count')} analysts)", snapshot))
    record = ref.get("earnings_record") or {}
    if record.get("quarters_count"):
        rows.append(("Earnings track record (EPS vs estimates)",
                     f"beat {record['beats']} of {record['quarters_count']} quarters, average surprise {record.get('avg_surprise_pct')}%", snapshot))
    nxt = ref.get("next_earnings") or {}
    if nxt.get("date"):
        rows.append(("Next earnings date", nxt["date"], snapshot))
    if ref.get("high_52w") and ref.get("low_52w"):
        rows.append(("52-week price range", f"{money(ref['low_52w'])} - {money(ref['high_52w'])}", snapshot))
    tech = ref.get("technicals") or {}
    if tech.get("sma200") and m["price"]:
        rows.append(("Price vs 200-day moving average", "above" if m["price"] > tech["sma200"] else "below", snapshot))
    insiders = ref.get("insiders") or []
    buys = sum(1 for t in insiders if t.get("code") == "P")
    sales = sum(1 for t in insiders if t.get("code") == "S")
    if insiders:
        rows.append(("Recent insider Form 4 filings (last 10)", f"{buys} open-market buys, {sales} open-market sales", sec))
    congress = ref.get("congress") or {}
    if congress.get("total_trades"):
        rows.append(("Insider and congressional trades on record",
                     f"{congress['total_trades']} trades: {congress.get('buys')} buys, {congress.get('sells')} sells", sec))

    facts = [{"id": f"F{i}", "label": label, "value": value, "source": source}
             for i, (label, value, source) in enumerate(rows, start=1)]
    curated = ref.get("curated") or {}
    curated_rows = []
    if curated.get("moat"):
        curated_rows.append(("Analyst-curated moat view", f"{curated['moat']} ({curated.get('moat_type') or ''})".strip()))
    curated_rows += [("Analyst-curated catalyst", c) for c in curated.get("catalysts") or []]
    curated_rows += [("Analyst-curated risk", r) for r in curated.get("risks") or []]
    facts += [{"id": f"C{i}", "label": label, "value": value, "source": "Stocks-Research curated profile"}
              for i, (label, value) in enumerate(curated_rows, start=1)]
    facts += [{"id": f"N{i}", "label": f"News ({n.get('date')}, {n.get('publisher')})", "value": n["title"], "source": n.get("url")}
              for i, n in enumerate(ref.get("news") or detail.get("news") or [], start=1)]
    release = detail.get("earnings_release")
    if release:
        facts.append({"id": "E1", "label": f"Latest earnings press release (8-K filed {release['filed']})",
                      "value": release["text"], "source": release["url"]})
    return facts


ANALYST_SYSTEM = """You are an equity analyst writing for one client: a long-term (5+ year), fundamentals-driven investor who looks for asymmetric opportunities — businesses where realistic upside is much larger than realistic downside. They hold a diversified index-fund core plus a few high-conviction stocks.

You receive a list of numbered facts (F = metrics and market data, C = curated research notes, N = news headlines, E = the company's latest earnings press release). Rules:
- Use ONLY these facts. Never introduce a number, date, or event that is not in a fact. When you mention a number, copy it exactly as written in the fact.
- Every bullet must cite the fact ids it relies on.
- Be specific and balanced. Write for an intelligent non-specialist, in short sentences.

Return a JSON object with exactly these keys:
{
  "one_liner": "one sentence thesis",
  "earnings_takeaways": [{"text": "...", "cites": ["E1"]}],   // 3 bullets: guidance, segment trends, margins. Empty list if no E1.
  "sentiment": {"score": -1.0 to 1.0, "reason": "...", "cites": [...]},   // from news and earnings tone
  "moat": {"rating": "none" | "narrow" | "wide", "reason": "...", "cites": [...]},
  "bull_points": [{"text": "...", "cites": [...]}],   // 2-4
  "bear_points": [{"text": "...", "cites": [...]}],   // 2-4
  "thesis_breakers": [{"text": "...", "cites": [...]}],   // 2-3 things that would make the client sell
  "conviction_adjustment": {"value": integer -10 to 10, "reason": "..."}   // your qualitative adjustment to the quant score
}"""


def _clamp_int(value, low, high):
    try:
        return max(low, min(high, int(round(float(value)))))
    except (TypeError, ValueError):
        return 0


def analyse_company(detail):
    facts = build_facts(detail)
    user = (f"Company: {detail['name']} ({detail['ticker']}), sector: {detail['sector']} ({detail['industry']}).\n\n"
            f"Facts:\n{json.dumps(facts, ensure_ascii=False)}")
    result = chat_json(ANALYST_SYSTEM, user)

    valid_ids = {f["id"] for f in facts}

    def clean(items):
        out = []
        for item in items or []:
            if isinstance(item, dict) and item.get("text"):
                out.append({"text": str(item["text"]), "cites": [c for c in item.get("cites", []) if c in valid_ids]})
        return out

    sentiment = result.get("sentiment") or {}
    moat = result.get("moat") or {}
    adjustment = result.get("conviction_adjustment") or {}
    return {
        "one_liner": re.sub(r"\s*\[(?:[A-Z]\d+[,;\s]*)+\]", "", str(result.get("one_liner", ""))).strip(),
        "earnings_takeaways": clean(result.get("earnings_takeaways")),
        "sentiment": {"score": max(-1.0, min(1.0, float(sentiment.get("score") or 0))),
                      "reason": str(sentiment.get("reason", "")),
                      "cites": [c for c in sentiment.get("cites", []) if c in valid_ids]},
        "moat": {"rating": moat.get("rating") if moat.get("rating") in ("none", "narrow", "wide") else "none",
                 "reason": str(moat.get("reason", "")),
                 "cites": [c for c in moat.get("cites", []) if c in valid_ids]},
        "bull_points": clean(result.get("bull_points")),
        "bear_points": clean(result.get("bear_points")),
        "thesis_breakers": clean(result.get("thesis_breakers")),
        "conviction_adjustment": {"value": _clamp_int(adjustment.get("value"), -10, 10),
                                  "reason": str(adjustment.get("reason", ""))},
        # Facts are returned so the UI can show what each citation points to (earnings text trimmed)
        "facts": [{**f, "value": f["value"][:600] + "…" if len(f["value"]) > 600 else f["value"]} for f in facts],
    }


MEMO_SYSTEM = """You write a short investment memo to one client: a long-term, fundamentals-driven investor seeking asymmetric opportunities, with an index-fund core for diversification.

Use ONLY the facts provided. Copy every number exactly as written in the facts; do not compute, round, or introduce any new number. Plain English, no jargon without explanation, about 220 words.

Structure: 1) the overall split and why (core vs high-conviction stocks), 2) the two or three biggest positions and the reason for each, 3) the main risks and what would make us change course, 4) one sentence noting this is an educational tool, not financial advice.

Return JSON: {"title": "...", "paragraphs": ["...", "..."]}"""


def memo_facts(profile, allocation, analyses):
    lines = [
        f"Amount to invest: {money(profile['amount'])}",
        f"Index core: {profile['etf']} at {pct(profile['core_pct'])} target",
        f"Investment horizon: {config.HORIZON_YEARS} years",
        f"Number of individual stocks: {sum(1 for h in allocation['holdings'] if h['kind'] == 'stock')}",
        f"Total invested: {money(allocation['invested'])}",
        f"Cash left over (whole shares only): {money(allocation['cash'])}",
    ]
    if allocation.get("weighted_asymmetry") is not None:
        lines.append(f"Weighted average asymmetry ratio of the stocks: {allocation['weighted_asymmetry']:.1f}x")
    for h in allocation["holdings"]:
        line = f"{h['ticker']} ({h['name']}, {h['sector']}): {money(h['amount'])}, {pct(h['weight'])} of the portfolio, {h['shares']} shares"
        if h["kind"] == "stock":
            line += f", asymmetry {h['asymmetry']:.1f}x, Skew score {h['final_score']:.1f}"
            a = analyses.get(h["ticker"]) or {}
            if a.get("one_liner"):
                line += f". Thesis: {a['one_liner']}"
            if a.get("bear_points"):
                line += f" Main risk: {a['bear_points'][0]['text']}"
        lines.append(line)
    return lines


def write_memo(profile, allocation, analyses):
    facts = memo_facts(profile, allocation, analyses)
    result = chat_json(MEMO_SYSTEM, "Facts:\n- " + "\n- ".join(facts), max_tokens=2000)
    paragraphs = [str(p) for p in result.get("paragraphs", []) if p]
    return {
        "title": str(result.get("title", "Your portfolio recommendation")),
        "paragraphs": paragraphs,
        "check": verify_numbers(" ".join(paragraphs), facts),
    }


NUMBER = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?")


def _numbers(text):
    out = []
    for token in NUMBER.findall(text):
        clean = token.replace("$", "").replace(",", "").lstrip("+")
        try:
            out.append((token, float(clean), len(clean.split(".")[1]) if "." in clean else 0))
        except ValueError:
            continue
    return out


def verify_numbers(text, facts):
    """Every number in `text` must appear in the facts (matching to the precision written)."""
    allowed = [value for _, value, _ in _numbers(" ".join(facts))]
    unverified = []
    for token, value, decimals in _numbers(text):
        if 1900 <= value <= 2100 and decimals == 0:
            continue  # years
        if not any(abs(round(a, decimals) - value) < 1e-9 or abs(a - value) < 1e-9 for a in allowed):
            unverified.append(token)
    total = len(_numbers(text))
    return {"total": total, "unverified": sorted(set(unverified)), "ok": not unverified}
