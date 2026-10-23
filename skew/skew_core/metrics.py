"""Deterministic metrics, quality score and bear/base/bull scenarios. No AI in this file."""

from . import config


def cagr(series, years=3):
    """Compound annual growth over the last `years` periods of [(date, value)]; None if not computable."""
    if len(series) < years + 1:
        return None
    start, end = series[-years - 1][1], series[-1][1]
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def _latest(series):
    return series[-1][1] if series else None


def _ratio(numerator, denominator):
    if numerator is None or not denominator:
        return None
    return numerator / denominator


def _by_date(series):
    return dict(series)


def margin_series(numerator, revenue):
    revenue_by_date = _by_date(revenue)
    return [(d, v / revenue_by_date[d]) for d, v in numerator if revenue_by_date.get(d)]


def compute_metrics(company):
    f = company["financials"]
    price = (company.get("price") or {}).get("price")
    revenue = f["revenue"]
    eps = _latest(f["eps"])

    op_margins = margin_series(f["operating_income"], revenue)
    cash_flow = _by_date(f["operating_cash_flow"])
    capex = _by_date(f["capex"])
    fcf = [(d, cash_flow[d] - capex.get(d, 0)) for d in cash_flow]
    fcf_margins = margin_series(sorted(fcf), revenue)

    debt = _latest(f["long_term_debt"]) or 0
    equity = _latest(f["equity"])
    pe = price / eps if price and eps and eps > 0 else None
    eps_growth = cagr(f["eps"])

    return {
        "price": price,
        "eps": eps,
        "pe": pe,
        "revenue_latest": _latest(revenue),
        "revenue_cagr_3y": cagr(revenue),
        "eps_cagr_3y": eps_growth,
        "operating_margin": _latest(op_margins),
        "operating_margin_change_3y": (op_margins[-1][1] - op_margins[-4][1]) if len(op_margins) >= 4 else None,
        "fcf_margin": _latest(fcf_margins),
        "debt_to_equity": debt / equity if equity and equity > 0 else None,
        "negative_equity": bool(equity is not None and equity <= 0),
        "peg": pe / (eps_growth * 100) if pe and eps_growth and eps_growth > 0 else None,
        "fiscal_year_end": revenue[-1][0] if revenue else None,
    }


def _scale(value, worst, best):
    """Map value linearly to 0..1, where `worst` -> 0 and `best` -> 1 (works for either direction)."""
    if value is None:
        return None
    t = (value - worst) / (best - worst)
    return max(0.0, min(1.0, t))


def _pillar(parts):
    present = [p for p in parts if p is not None]
    # Missing inputs count as zero so incomplete data can't look better than complete data
    return 25 * sum(present) / len(parts), len(present), len(parts)


def score(metrics):
    m = metrics
    if m["negative_equity"]:
        balance = 0.4  # usually buyback-driven, not distress, but riskier to model
    elif m["debt_to_equity"] is None:
        balance = None
    else:
        balance = _scale(m["debt_to_equity"], 2.0, 0.0)

    pillars = {
        "growth": _pillar([_scale(m["revenue_cagr_3y"], 0.0, 0.25), _scale(m["eps_cagr_3y"], 0.0, 0.30)]),
        "quality": _pillar([_scale(m["operating_margin"], 0.05, 0.40), _scale(m["fcf_margin"], 0.0, 0.30),
                            _scale(m["operating_margin_change_3y"], -0.05, 0.05)]),
        "balance_sheet": _pillar([balance]),
        "valuation": _pillar([_scale(m["peg"], 3.0, 1.0)]),
    }
    present = sum(p[1] for p in pillars.values())
    total = sum(p[2] for p in pillars.values())
    return {
        "total": round(sum(p[0] for p in pillars.values()), 1),
        "pillars": {name: round(p[0], 1) for name, p in pillars.items()},
        "confidence": round(present / total, 2),
    }


def scenarios(metrics, years=config.HORIZON_YEARS):
    """5-year bear/base/bull prices from EPS growth and exit P/E; the asymmetry ratio compares them."""
    price, eps, pe = metrics["price"], metrics["eps"], metrics["pe"]
    if not price or not eps or eps <= 0 or not pe:
        return None
    # EPS growth can't be computed when an earlier year was a loss; revenue growth is the next-best proxy
    if metrics["eps_cagr_3y"] is not None:
        g, growth_basis = metrics["eps_cagr_3y"], "EPS"
    elif metrics["revenue_cagr_3y"] is not None:
        g, growth_basis = metrics["revenue_cagr_3y"], "revenue"
    else:
        g, growth_basis = 0.0, "none"
    base_growth = max(-0.10, min(g, 0.20))
    cases = {
        # Bear: growth halves and is capped at 8%/yr, and the multiple compresses to 15x
        "bear": {"growth": min(base_growth, max(-0.05, g / 2), 0.08), "exit_pe": min(pe, 15)},
        "base": {"growth": base_growth, "exit_pe": min(pe, 25)},
        "bull": {"growth": max(base_growth, min(g, 0.35)), "exit_pe": min(pe, 35)},
    }
    for case in cases.values():
        case["eps"] = eps * (1 + case["growth"]) ** years
        case["price"] = case["eps"] * case["exit_pe"]
        case["return"] = case["price"] / price - 1

    upside, downside = cases["bull"]["return"], cases["bear"]["return"]
    if upside <= 0:
        ratio = 0.0
    elif downside >= 0:
        ratio = 10.0  # no modelled downside: cap rather than divide by zero
    else:
        ratio = min(10.0, upside / -downside)
    return {"cases": cases, "upside": upside, "downside": downside, "asymmetry": round(ratio, 2), "years": years,
            "growth_basis": growth_basis, "growth_input": g}


def analyse(company):
    metrics = compute_metrics(company)
    return {"metrics": metrics, "score": score(metrics), "scenarios": scenarios(metrics)}
