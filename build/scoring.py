import math

import config


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def field(record, name, default=None):
    return record.get("fields", {}).get(name, {}).get("value", default)


def annual_values(record, name):
    return {
        observation["year"]: observation["value"]
        for observation in field(record, name, []) or []
        if finite(observation.get("value"))
    }


def cagr(start, end, years=3):
    if not finite(start) or not finite(end) or years <= 0 or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def series_cagr(values):
    if not values:
        return None
    year = max(values)
    return cagr(values.get(year - 3), values[year], 3)


def divide(numerator, denominator, positive_denominator=True):
    if not finite(numerator) or not finite(denominator):
        return None
    if denominator == 0 or (positive_denominator and denominator < 0):
        return None
    return numerator / denominator


def peg(pe, eps_cagr):
    if not finite(pe) or pe <= 0 or not finite(eps_cagr) or eps_cagr <= 0:
        return None
    return pe / (eps_cagr * 100)


def clipped(value):
    return max(0.0, min(1.0, value))


def normalized(value, scale, inverse=False):
    if not finite(value):
        return 0.0
    score = value / scale
    return clipped(1 - score if inverse else score)


def compute_metrics(record):
    series = {name: annual_values(record, name) for name in config.FINANCIAL_TAGS}
    revenues = series["revenue"]
    year = max(revenues) if revenues else None
    revenue = revenues.get(year)
    operating_margin = divide(series["operating_income"].get(year), revenue)
    old_margin = divide(
        series["operating_income"].get(year - 3) if year is not None else None,
        revenues.get(year - 3) if year is not None else None,
    )
    cash_flow = series["operating_cash_flow"].get(year)
    capex = series["capex"].get(year)
    fcf = cash_flow - abs(capex) if finite(cash_flow) and finite(capex) else None
    equity_year = max(series["equity"]) if series["equity"] else None
    eps_year = max(series["eps"]) if series["eps"] else None
    eps_growth = series_cagr(series["eps"])
    return {
        "revenue_cagr": series_cagr(revenues),
        "eps_cagr": eps_growth,
        "operating_margin": operating_margin,
        "operating_margin_trend": (
            operating_margin - old_margin
            if operating_margin is not None and old_margin is not None else None
        ),
        "fcf_margin": divide(fcf, revenue),
        "debt_equity": divide(
            series["debt"].get(equity_year), series["equity"].get(equity_year)
        ),
        "peg": peg(field(record, "trailing_pe"), eps_growth),
        "latest_eps": series["eps"].get(eps_year),
        "revenue_year": year,
        "eps_year": eps_year,
        "balance_year": equity_year,
    }


def quant_score(metrics):
    growth = (
        normalized(metrics.get("revenue_cagr"), 0.20)
        + normalized(metrics.get("eps_cagr"), 0.20)
    ) / 2
    quality = (
        normalized(metrics.get("operating_margin"), 0.30)
        + normalized(metrics.get("fcf_margin"), 0.25)
    ) / 2
    balance = normalized(metrics.get("debt_equity"), 2.0, inverse=True)
    valuation = normalized(metrics.get("peg"), 3.0, inverse=True)
    pillars = {
        "Growth": 25 * growth,
        "Quality": 25 * quality,
        "Balance sheet": 25 * balance,
        "Valuation": 25 * valuation,
    }
    components = [
        "revenue_cagr", "eps_cagr", "operating_margin",
        "fcf_margin", "debt_equity", "peg",
    ]
    confidence = sum(finite(metrics.get(key)) for key in components) / len(components)
    return {"quant_score": sum(pillars.values()), "pillars": pillars, "confidence": confidence}


def scenarios(price, eps, eps_growth, current_pe):
    result = {
        "bear": None, "base": None, "bull": None, "upside": None,
        "downside": None, "asymmetry_ratio": None, "paths": {}, "reason": None,
    }
    if not all(finite(value) for value in (price, eps, eps_growth, current_pe)):
        result["reason"] = "Missing price, annual EPS, positive-endpoint EPS CAGR, or trailing P/E."
        return result
    if price <= 0 or eps <= 0 or current_pe <= 0 or eps_growth <= -1:
        result["reason"] = "Scenario inputs require positive price, EPS and P/E."
        return result
    growths = {
        "bear": max(0.0, eps_growth / 2),
        "base": min(eps_growth, 0.20),
        "bull": min(eps_growth, 0.35),
    }
    multiples = {
        "bear": min(current_pe, 15),
        "base": min(current_pe, 25),
        "bull": min(current_pe, 35),
    }
    for case in ("bear", "base", "bull"):
        target = eps * (1 + growths[case]) ** config.SCENARIO_YEARS * multiples[case]
        result[case] = target
        result["paths"][case] = {
            "eps_growth": growths[case], "exit_pe": multiples[case],
            "terminal_eps": eps * (1 + growths[case]) ** config.SCENARIO_YEARS,
        }
    result["upside"] = result["bull"] / price - 1
    result["downside"] = result["bear"] / price - 1
    if result["downside"] >= 0:
        result["reason"] = "Bear target shows no loss; a meaningful downside ratio cannot be estimated."
    else:
        result["asymmetry_ratio"] = result["upside"] / abs(result["downside"])
    return result


def analyze_record(record):
    metrics = compute_metrics(record)
    return {
        "ticker": record["ticker"],
        "price": field(record, "price"),
        "sector": field(record, "sector") or "Unknown",
        "metrics": metrics,
        **quant_score(metrics),
        "scenarios": scenarios(
            field(record, "price"), metrics["latest_eps"],
            metrics["eps_cagr"], field(record, "trailing_pe"),
        ),
        "fetch_errors": record.get("errors", []),
    }
