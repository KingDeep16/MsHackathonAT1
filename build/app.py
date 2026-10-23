import json
import re
from datetime import datetime, timezone

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

import config
from ai_analyst import analyze_top, write_memo
from allocation import allocate, final_score
from data_sources import fetch_universe
from scoring import analyze_record, annual_values, field
from storage import read_json, write_json

st.set_page_config(page_title="Skew · Invest with asymmetry", page_icon="◒", layout="wide")
st.markdown("""
<style>
.block-container {padding-top: 2rem; padding-bottom: 3rem; max-width: 1450px;}
h1 {letter-spacing: -0.06em;}
[data-testid="stMetricValue"] {font-size: 1.7rem;}
[data-testid="stMetric"] {
    background: rgba(120,140,160,.07);
    padding: 1rem; border-radius: 12px;
}
.stCaption {line-height: 1.5;}
@media (max-width: 640px) {
    .block-container {padding-left: 1rem; padding-right: 1rem;}
}
</style>
""", unsafe_allow_html=True)


def money(value):
    return f"${value:,.0f}" if value is not None else "—"


def percent(value):
    return f"{value:.1%}" if value is not None else "—"


def multiple(value, cap=False):
    if value is None:
        return "—"
    if cap and value > config.DISPLAY_ASYMMETRY_CAP:
        return f"{config.DISPLAY_ASYMMETRY_CAP:.0f}×+"
    return f"{value:.2f}×"


def parse_universe(text, etf):
    tickers = list(dict.fromkeys(
        token.upper().replace(".", "-")
        for token in re.split(r"[\s,;]+", text.strip()) if token
    ))
    if not tickers:
        raise ValueError("Enter at least one stock ticker.")
    if len(tickers) > 60:
        raise ValueError("Use at most 60 tickers for a reliable live demo.")
    invalid = [ticker for ticker in tickers if not re.fullmatch(r"[A-Z]{1,6}(?:-[A-Z])?", ticker)]
    if invalid:
        raise ValueError("Invalid ticker(s): " + ", ".join(invalid))
    if etf in tickers:
        raise ValueError(f"Remove {etf} from the stock universe; it is already the index core.")
    return tickers


def run_pipeline(settings, refresh, progress):
    records = fetch_universe(
        settings["tickers"], settings["etf"], refresh=refresh,
        progress=lambda done, total, ticker: progress.progress(
            0.05 + 0.50 * done / total, text=f"Public data · {ticker} · {done}/{total}"
        ),
    )
    analyses = [analyze_record(records[ticker]) for ticker in settings["tickers"]]
    progress.progress(0.56, text="Quantitative scores and five-year scenarios")
    analyses = analyze_top(
        analyses, records,
        progress=lambda done, total, ticker: progress.progress(
            0.56 + 0.31 * done / total, text=f"AI analyst · {ticker} · {done}/{total}"
        ),
    )
    for analysis in analyses:
        analysis["final_score"] = final_score(analysis)
    progress.progress(0.89, text="Applying stock and sector caps")
    allocation = allocate(
        analyses, settings["amount"], settings["core_pct"], settings["etf"],
        field(records[settings["etf"]], "price"), settings["n_stocks"],
        settings["stock_cap"], settings["sector_cap"],
    )
    progress.progress(0.93, text="Writing client memo and checking every numeric figure")
    memo = write_memo(allocation, analyses)
    return {
        "schema_version": config.RUN_SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "settings": settings, "records": records, "analyses": analyses,
        "allocation": allocation, "memo": memo,
    }


if "last_run" not in st.session_state:
    saved = read_json(config.LAST_RUN_PATH)
    if (
        isinstance(saved, dict)
        and saved.get("schema_version") == config.RUN_SCHEMA_VERSION
        and all(key in saved for key in ("settings", "records", "analyses", "allocation", "memo"))
    ):
        st.session_state.last_run = saved
    else:
        st.session_state.last_run = None

last = st.session_state.last_run
previous = last["settings"] if last else {}

with st.sidebar:
    st.title("◒ Skew")
    st.caption("The patient, asymmetric investor")
    st.markdown("**5+ years · Fundamentals first · Core–satellite**")
    amount = st.number_input(
        "Portfolio amount (US$)", min_value=1000.0, max_value=100_000_000.0,
        value=float(previous.get("amount", config.DEFAULT_AMOUNT)), step=1000.0,
    )
    core_pct = st.slider(
        "Index core (%)", 0, 100, int(round(100 * previous.get("core_pct", config.DEFAULT_CORE)))
    ) / 100
    selected_etf = previous.get("etf", config.DEFAULT_ETF)
    etf = st.selectbox("Index ETF", config.ETF_CHOICES, index=config.ETF_CHOICES.index(selected_etf))
    n_stocks = st.slider("Satellite stocks", 6, 10, previous.get("n_stocks", config.DEFAULT_STOCKS))
    stock_cap = st.slider(
        "Max per stock (% of total)", 5, 30,
        int(round(100 * previous.get("stock_cap", config.DEFAULT_STOCK_CAP))),
    ) / 100
    sector_cap = st.slider(
        "Max per sector (% of total)", 10, 60,
        int(round(100 * previous.get("sector_cap", config.DEFAULT_SECTOR_CAP))),
    ) / 100
    st.caption("Caps apply to satellites only. Unallocatable funds remain cash.")
    universe = st.text_area(
        "Stock universe", ", ".join(previous.get("tickers", config.DEFAULT_UNIVERSE)), height=165
    )
    run_clicked = st.button("Run analysis", type="primary", use_container_width=True)
    refresh_clicked = st.button("Refresh data", use_container_width=True)
    st.caption("Run uses data younger than 24h. Refresh bypasses the ticker cache.")
    if not config.SEC_USER_AGENT:
        st.error("SEC_USER_AGENT is missing. Set your name and email before fetching SEC data.")
    if not config.OPENAI_API_KEY:
        st.warning("No OPENAI_API_KEY: quantitative mode; AI and memo unavailable.")
    st.caption(f"Model: {config.LLM_MODEL}")
    if last:
        st.caption(f"Last completed run: {last['created_at'][:19]} UTC")
        timestamps = [
            record["fetched_at"] for record in last["records"].values()
            if record.get("fetched_at")
        ]
        if timestamps:
            st.caption(f"Oldest data fetch: {min(timestamps)[:19]} UTC")

st.title("Skew")
st.markdown("### Own the upside. Understand the downside.")
st.caption("A source-linked investment research demo for one patient, fundamentals-driven client.")

if run_clicked or refresh_clicked:
    progress = st.progress(0.0, text="Preparing analysis")
    try:
        settings = {
            "amount": amount, "core_pct": core_pct, "etf": etf,
            "n_stocks": n_stocks, "stock_cap": stock_cap, "sector_cap": sector_cap,
            "tickers": parse_universe(universe, etf),
        }
        result = run_pipeline(settings, refresh_clicked, progress)
        st.session_state.last_run = result
        last = result
        try:
            write_json(config.LAST_RUN_PATH, result)
        except OSError:
            st.warning("Completed, but data/last_run.json could not be saved. Check directory permissions.")
        progress.progress(1.0, text="Analysis complete")
    except Exception as error:
        if isinstance(error, ValueError):
            st.error(str(error))
        else:
            st.error(f"Run interrupted ({type(error).__name__}). The previous completed run is preserved.")
    finally:
        progress.empty()

recommendation, deep_dive, screened_out, method = st.tabs(
    ["Recommendation", "Deep dive", "Screened out", "Method"]
)


def render_bullet(bullet):
    st.write("• " + bullet["text"])
    citations = bullet.get("citations", [])
    st.caption("Sources: " + " · ".join(f"[{citation}](#fact-{citation.lower()})" for citation in citations))


def render_facts(facts):
    with st.expander("Source ledger · cited facts, raw observations and URLs"):
        st.caption(
            "F = reported or computed financial fact; N = news; E = earnings release. "
            "Derived facts link to their public input filings; formulas are in Method."
        )
        for fact_id, fact in facts.items():
            st.markdown(f'<div id="fact-{fact_id.lower()}"></div>', unsafe_allow_html=True)
            st.markdown(f"**{fact_id} · {fact['label']}**")
            value = fact.get("value")
            if isinstance(value, (dict, list)):
                st.json(value, expanded=False)
            elif value is None:
                st.caption("Unavailable")
            else:
                st.text(str(value))
            source = fact.get("source")
            urls = source if isinstance(source, list) else [source]
            for index, url in enumerate(urls, 1):
                if isinstance(url, str) and url.startswith(("https://", "http://")):
                    st.markdown(f"[Public source {index}]({url})")
            if fact.get("exhibit_url"):
                st.markdown(f"[Exhibit 99.1]({fact['exhibit_url']})")
            if fact.get("facts_url"):
                st.markdown(f"[SEC XBRL feed]({fact['facts_url']})")
            metadata = {
                key: value for key, value in fact.items()
                if key not in ("label", "value", "source", "exhibit_url", "facts_url")
                and value is not None
            }
            if metadata:
                st.caption(json.dumps(metadata))


with recommendation:
    if not last:
        st.info("Set SEC_USER_AGENT, then choose Run analysis. A completed run reloads instantly without network.")
    else:
        allocation = last["allocation"]
        st.caption(
            f"Saved recommendation · {last['created_at'][:19]} UTC · "
            f"{money(allocation['amount'])} portfolio. Sidebar edits apply only after a new run."
        )
        columns = st.columns(4)
        columns[0].metric("Total invested", money(allocation["invested"]))
        columns[1].metric("Funded holdings", len(allocation["holdings"]))
        columns[2].metric("Weighted asymmetry", multiple(allocation["weighted_asymmetry"], cap=True))
        columns[3].metric("Weighted EPS CAGR", percent(allocation["weighted_eps_cagr"]))
        st.caption(
            "Weighted KPIs cover funded satellites only and exclude the ETF/cash. "
            "Asymmetry uses uncapped ratios in calculations; display stops at 10×+."
        )
        st.markdown(f"**Leftover cash: {money(allocation['cash'])}**")
        for note in allocation["notes"]:
            st.info(note)
        holdings = allocation["holdings"]
        if holdings:
            left, right = st.columns([1, 1.7])
            with left:
                slices = [{"Holding": holding["ticker"], "USD": holding["amount"]} for holding in holdings]
                if allocation["cash"] > 0:
                    slices.append({"Holding": "Cash", "USD": allocation["cash"]})
                figure = px.pie(
                    pd.DataFrame(slices), names="Holding", values="USD", hole=0.66,
                    color_discrete_sequence=px.colors.qualitative.Safe,
                )
                figure.update_layout(margin=dict(l=5, r=5, t=20, b=10), height=350)
                figure.update_traces(textinfo="label+percent")
                st.plotly_chart(figure, use_container_width=True)
            with right:
                rows = [
                    {
                        "Ticker": holding["ticker"], "Sector": holding["sector"],
                        "Amount": money(holding["amount"]), "Shares": holding["shares"],
                        "Weight": percent(holding["weight"]),
                        "Final score": (
                            f"{holding['final_score']:.1f}" if holding["final_score"] is not None else "—"
                        ),
                        "Asymmetry": multiple(holding["asymmetry_ratio"], cap=True),
                    } for holding in holdings
                ]
                st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
                st.caption("Whole shares at the fetched quote; excludes transaction costs and future price movement.")
        else:
            st.warning("No funded holdings. Inspect the screening reasons and data errors before retrying.")

        st.subheader("Your investment memo")
        memo = last["memo"]
        if memo.get("text"):
            st.write(memo["text"])
            check = memo["verification"]
            if check["verified"] and check["checked_count"]:
                st.success("All figures verified against source data")
            elif check["unverified"]:
                st.warning("Unverified numbers: " + ", ".join(check["unverified"]))
            else:
                st.info("The memo contains no numeric figures to verify.")
            st.caption(
                "Verification checks numeric occurrence within rounding against the supplied "
                "allocation and analysis facts. It does not verify units, reasoning or causal claims."
            )
        else:
            st.info(memo.get("error", "Client memo unavailable."))
        with st.expander("Memo facts and number-check result"):
            st.json({"facts": memo.get("facts"), "verification": memo.get("verification")}, expanded=False)
        with st.expander("Quote provenance · including the index core"):
            for ticker in [holding["ticker"] for holding in holdings]:
                quote = last["records"][ticker].get("fields", {}).get("price", {})
                st.markdown(f"**{ticker}** · {quote.get('value', 'Unavailable')}")
                if quote.get("source"):
                    st.markdown(f"[Yahoo quote]({quote['source']})")
                st.caption(last["records"][ticker].get("fetched_at", ""))
        st.download_button(
            "Download full research audit (JSON)",
            json.dumps(last, indent=2, allow_nan=False),
            file_name="skew-research.json", mime="application/json",
        )

with deep_dive:
    if not last:
        st.info("Run an analysis to inspect company fundamentals.")
    else:
        choices = sorted(last["records"])
        ticker = st.selectbox("Company / core ETF", choices)
        record = last["records"][ticker]
        for error in record.get("errors", []):
            st.warning(error)
        if record.get("is_etf"):
            st.metric("Index ETF price", money(field(record, "price")))
            st.caption("The diversified core is not scored or assigned individual-company EPS scenarios.")
            st.json(record, expanded=False)
        else:
            analysis = next(item for item in last["analyses"] if item["ticker"] == ticker)
            metrics = analysis["metrics"]
            scenario = analysis["scenarios"]
            columns = st.columns(4)
            columns[0].metric("Asymmetry", multiple(scenario["asymmetry_ratio"], cap=True))
            columns[1].metric("Quant score", f"{analysis['quant_score']:.1f} / 100")
            columns[2].metric("Final score", f"{analysis['final_score']:.1f}")
            columns[3].metric("Data confidence", percent(analysis["confidence"]))
            columns = st.columns(4)
            columns[0].metric("Revenue · 3y CAGR", percent(metrics["revenue_cagr"]))
            columns[1].metric("EPS · 3y CAGR", percent(metrics["eps_cagr"]))
            columns[2].metric("Operating margin", percent(metrics["operating_margin"]))
            trend = metrics["operating_margin_trend"]
            columns[3].metric("Margin · 3y change", f"{100 * trend:+.1f} pp" if trend is not None else "—")
            columns = st.columns(4)
            columns[0].metric("FCF margin", percent(metrics["fcf_margin"]))
            columns[1].metric("Debt / equity", multiple(metrics["debt_equity"]))
            columns[2].metric("PEG", multiple(metrics["peg"]))
            columns[3].metric("Fetched price", money(analysis["price"]))
            st.caption(
                f"Revenue fiscal-end year: {metrics['revenue_year'] or 'unavailable'} · "
                f"EPS fiscal-end year: {metrics['eps_year'] or 'unavailable'} · "
                f"Balance-sheet year: {metrics['balance_year'] or 'unavailable'} · "
                f"Fetched {record['fetched_at'][:19]} UTC"
            )
            with st.expander("Additional market data and pillar scores"):
                st.json({
                    "market_data": {
                        key: record.get("fields", {}).get(key)
                        for key in ("sector", "market_cap", "trailing_pe", "forward_pe", "high_52w", "low_52w")
                    },
                    "pillars_out_of_25": analysis["pillars"],
                }, expanded=False)

            revenue = annual_values(record, "revenue")
            eps = annual_values(record, "eps")
            if revenue or eps:
                chart = make_subplots(rows=1, cols=2, subplot_titles=("Revenue · US$ billions", "Diluted EPS · US$"))
                chart.add_trace(
                    go.Bar(x=[str(year) for year in sorted(revenue)],
                           y=[revenue[year] / 1e9 for year in sorted(revenue)],
                           name="Revenue", marker_color="#3478c0"),
                    row=1, col=1,
                )
                chart.add_trace(
                    go.Bar(x=[str(year) for year in sorted(eps)],
                           y=[eps[year] for year in sorted(eps)],
                           name="EPS", marker_color="#20a387"),
                    row=1, col=2,
                )
                chart.update_layout(height=330, showlegend=False, margin=dict(t=40, b=25))
                chart.update_xaxes(type="category", title_text="Fiscal end year")
                st.plotly_chart(chart, use_container_width=True)
            else:
                st.info("No supported annual US-GAAP 10-K history is available.")

            st.subheader("Five-year scenario map")
            if scenario["bear"] is not None:
                chart = go.Figure(go.Bar(
                    x=["Bear", "Base", "Bull"],
                    y=[scenario[key] for key in ("bear", "base", "bull")],
                    marker_color=["#d56b61", "#547fa5", "#20a387"],
                    text=[money(scenario[key]) for key in ("bear", "base", "bull")],
                    textposition="auto",
                ))
                chart.add_hline(
                    y=analysis["price"], line_dash="dash",
                    annotation_text=f"Current {money(analysis['price'])}",
                )
                chart.update_layout(height=330, yaxis_title="Price target · US$", margin=dict(t=30, b=20))
                st.plotly_chart(chart, use_container_width=True)
                st.caption(
                    f"Bull upside {percent(scenario['upside'])} · "
                    f"Bear return {percent(scenario['downside'])}. "
                    "Mechanical scenarios, not probabilities, fair values, or guaranteed outcomes."
                )
                with st.expander("Scenario EPS growth and exit multiples"):
                    st.json(scenario, expanded=False)
            if scenario.get("reason"):
                st.warning(scenario["reason"])

            st.subheader("AI analyst")
            ai = analysis["ai"]
            if ai["status"] != "available":
                st.info(ai["message"])
            else:
                st.caption(
                    f"Sentiment: {ai['sentiment']['score']:+.2f} · "
                    f"Moat: {ai['moat']['rating']} · "
                    f"Conviction overlay: {ai['conviction_adjustment']['value']:+d}"
                )
                for label, key in [
                    ("Latest earnings", "earnings_takeaways"),
                    ("Bull case", "bull_points"), ("Bear case", "bear_points"),
                    ("Thesis breakers", "thesis_breakers"),
                ]:
                    st.markdown(f"**{label}**")
                    for bullet in ai[key]:
                        render_bullet(bullet)
                for label, key in [
                    ("Sentiment reasoning", "sentiment"),
                    ("Moat reasoning", "moat"),
                    ("Conviction reasoning", "conviction_adjustment"),
                ]:
                    st.markdown(f"**{label}**")
                    render_bullet(ai[key]["reason"])
            st.caption("Open the source ledger below before following an F/N/E citation.")
            render_facts(analysis["facts"])

with screened_out:
    if not last:
        st.info("Screening results appear after an analysis.")
    else:
        reasons = last["allocation"]["screened"]
        if reasons:
            st.dataframe(
                pd.DataFrame([
                    {
                        "Ticker": row["ticker"], "Reason": row["reason"],
                        "Fetch errors": " | ".join(row.get("fetch_errors", [])),
                    } for row in reasons
                ]),
                hide_index=True, use_container_width=True,
            )
        else:
            st.success("All screened stocks were funded.")
        with st.expander("All source warnings, including partially successful tickers", expanded=True):
            warnings = [
                {"Ticker": ticker, "Warning": error}
                for ticker, record in last["records"].items()
                for error in record.get("errors", [])
            ]
            if warnings:
                st.dataframe(pd.DataFrame(warnings), hide_index=True, use_container_width=True)
            else:
                st.caption("No fetch errors were recorded.")

with method:
    st.markdown("""
### Five steps
1. **Gather public evidence.** Fetch Yahoo quotes, SEC annual facts and the latest
   earnings 8-K exhibit, plus recent headlines. Cache each ticker for 24 hours.
2. **Measure fundamentals.** Calculate growth, margins, leverage and PEG, then four
   equally weighted quantitative pillars. Missing components score zero.
3. **Model the skew.** Compound annual diluted EPS for five years and apply
   deterministic exit multiples. Screen for a bull-upside / bear-loss ratio of at least 1.5×.
4. **Challenge the thesis.** Analyze the top 12 quantitative names with the AI,
   requiring fact citations. Add its bounded conviction overlay and asymmetry bonus.
5. **Build and explain.** Allocate the index core and top-N satellites, enforce
   caps, round down to whole shares, and check the memo's numbers against its facts.

### Financial formulas
- **3y CAGR:** `(latest / three-years-earlier)^(1/3) − 1`. Both endpoints must
  be positive; the exact earlier fiscal-end year must exist.
- **Operating margin:** `operating income / revenue`.
- **3y margin trend:** latest operating margin minus the margin three years
  earlier, displayed in percentage points.
- **Free cash flow margin:** `(operating cash flow − abs(capex)) / revenue`.
- **Debt/equity:** reported long-term debt divided by positive shareholders' equity.
  This is **not total liabilities/equity**; the noncurrent-debt fallback can omit current maturities.
- **PEG:** positive trailing P/E divided by positive EPS CAGR expressed as a
  percentage (for example, a fractional CAGR of 0.20 is a denominator of 20).
- Margins align to the latest revenue year. Debt aligns to the latest equity year.
  Missing aligned values are not replaced with older observations.

### Quant score: 0–100
Let `clip(x) = max(0, min(1, x))`; all growth and margin inputs below are fractions.

| Pillar | Points, maximum 25 |
|---|---|
| Growth | `25 × mean(clip(revenue CAGR / 0.20), clip(EPS CAGR / 0.20))` |
| Quality | `25 × mean(clip(operating margin / 0.30), clip(FCF margin / 0.25))` |
| Balance sheet | `25 × clip(1 − debt/equity / 2)` |
| Valuation | `25 × clip(1 − PEG / 3)` |

A missing component contributes zero, including a missing inverse-ratio component.
There is no reweighting to hide gaps. **Confidence** is the share of the six
scored inputs available; it is a completeness measure, not a probability.
Margin trend is contextual evidence, not an additional scored component.

### Five-year scenarios
Starting EPS is the latest annual diluted EPS, **not TTM EPS**.

| | Annual EPS growth | Exit P/E |
|---|---|---|
| Bear | `max(0, EPS CAGR / 2)` | `min(current trailing P/E, 15)` |
| Base | `min(EPS CAGR, 0.20)` | `min(current trailing P/E, 25)` |
| Bull | `min(EPS CAGR, 0.35)` | `min(current trailing P/E, 35)` |

`target = annual EPS × (1 + growth)^5 × exit P/E`

`upside = bull / price − 1`; `downside = bear / price − 1`

**Asymmetry = upside / abs(downside)**. Calculations retain the uncapped ratio;
display is capped at 10×+. When the bear return is nonnegative, the ratio is
unavailable and the stock is excluded instead of displaying a misleading infinity.
Negative historical growth can make the mechanically named scenarios cross;
the chart is not reordered to conceal that limitation.

### Allocation
`final score = quant score + AI conviction adjustment + min(10, max(0, 2 × asymmetry))`

The AI adjustment is an integer from −10 to +10, or zero when unavailable.
Final scores are not capped at 100. Ties break alphabetically.
Stocks need a positive price, positive final score and asymmetry of at least 1.5×.
The top N eligible stocks receive satellite dollars proportional to final score.
Weighted water filling redistributes excess until individual-stock or sector
capacity is exhausted. Sector labels come from Yahoo; unknown sectors share one
“Unknown” bucket.

Caps are fractions of the **original portfolio amount**, not satellite value.
The ETF core is exempt from both caps. The implementation does not perform ETF
sector look-through. Whole-share rounding leaves cash and cannot breach a cap.
If constraints cannot fund the requested stock count or budget, cash remains;
the rules are never silently relaxed. This is a proposed snapshot, not an order.

### Data and traceability
- [Yahoo Finance](https://finance.yahoo.com/) via yfinance: price, sector,
  market cap, P/E, 52-week range and fallback news.
- [SEC EDGAR](https://www.sec.gov/edgar/search/): ticker mapping,
  US-GAAP company facts, submissions and earnings-release exhibits.
- [Finnhub](https://finnhub.io/docs/api/company-news): headlines when a key is set.
- Every fetched observation includes its provider and public source URL.
  The source ledger contains raw financial observations, computed facts and
  the earnings excerpt sent to the analyst. Download the complete run for audit.
- Annual facts are 10-K/FY observations with annual durations. Fiscal-end calendar
  year labels prevent comparative periods from being mislabeled as filing years.
  Latest-filed restatements win within each tag; configured tag priority resolves overlaps.
- The most recent item-2.02 8-K in the SEC recent-submissions window is inspected.
  EX-99.1 identification failures remain explicit missing data.

### Limitations
This is a transparent screening heuristic, not a calibrated valuation model.
It uses historical growth, annual EPS and live/delayed quotes of differing dates.
Share splits, acquisitions, stock-based compensation, fiscal-calendar changes,
restated history, custom XBRL tags and differing accounting conventions can distort
comparisons. Ratios should be reviewed against filings before relying on them.

Foreign 20-F/6-K filers and IFRS-only histories are not synthesized into US-GAAP
10-K data. Annual dollars/shares can differ from ADR quote units. Sector labels
are broad. Headlines are not full articles; the earnings excerpt is truncated.
AI citations are validated for existence, not entailment. AI outputs can vary
between runs and are not independently verified company research.

Memo verification is a regex-based numeric occurrence/rounding check against
the exact supplied allocation and analysis facts. It does not certify factual
meaning, units, claims, or investment suitability. Unverified figures are shown,
not silently corrected.

Scenarios omit dividends, taxes, fees, inflation, FX, dilution and probabilities.
No optimization guarantees diversification inside the ETF or the requested
number of holdings. The app uses public sources; it does not claim an LSEG data
connection. A saved run reloads without network but is an explicitly dated snapshot.
""")

st.divider()
st.caption("Educational project, not financial advice.")
