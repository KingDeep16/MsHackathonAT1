import math

import config
from scoring import finite


def asymmetry_bonus(ratio):
    return min(10.0, max(0.0, 2 * ratio)) if finite(ratio) else 0.0


def final_score(analysis):
    adjustment = analysis.get("ai", {}).get("conviction_adjustment", {}).get("value", 0)
    if not isinstance(adjustment, int) or isinstance(adjustment, bool):
        adjustment = 0
    adjustment = max(-10, min(10, adjustment))
    return (
        analysis["quant_score"] + adjustment
        + asymmetry_bonus(analysis["scenarios"].get("asymmetry_ratio"))
    )


def constrained_targets(stocks, budget, stock_limit, sector_limit):
    """Weighted water filling with simultaneous stock and sector constraints."""
    targets = {stock["ticker"]: 0.0 for stock in stocks}
    remaining = budget
    for _ in range(2 * len(stocks) + 2):
        sector_totals = {}
        for stock in stocks:
            sector = stock["sector"]
            sector_totals[sector] = sector_totals.get(sector, 0.0) + targets[stock["ticker"]]
        active = [
            stock for stock in stocks
            if stock["final_score"] > 0
            and stock_limit - targets[stock["ticker"]] > 1e-7
            and sector_limit - sector_totals[stock["sector"]] > 1e-7
        ]
        if remaining < 1e-7 or not active:
            break
        total_score = sum(stock["final_score"] for stock in active)
        step = remaining / total_score
        sector_scores = {}
        for stock in active:
            score = stock["final_score"]
            step = min(step, (stock_limit - targets[stock["ticker"]]) / score)
            sector = stock["sector"]
            sector_scores[sector] = sector_scores.get(sector, 0.0) + score
        for sector, score in sector_scores.items():
            step = min(step, (sector_limit - sector_totals[sector]) / score)
        distributed = 0.0
        for stock in active:
            addition = max(0.0, step * stock["final_score"])
            targets[stock["ticker"]] += addition
            distributed += addition
        remaining = max(0.0, remaining - distributed)
    return targets


def allocate(analyses, amount, core_pct, etf, etf_price, n_stocks,
             stock_cap, sector_cap):
    if not finite(amount) or amount <= 0:
        raise ValueError("Investment amount must be positive.")
    if not 0 <= core_pct <= 1 or not 0 < stock_cap <= 1 or not 0 < sector_cap <= 1:
        raise ValueError("Core and cap fractions must be between zero and one.")
    if not isinstance(n_stocks, int) or n_stocks < 1:
        raise ValueError("Choose at least one stock.")

    candidates = []
    screened = []
    for original in analyses:
        stock = dict(original)
        stock["final_score"] = final_score(stock)
        ratio = stock["scenarios"].get("asymmetry_ratio")
        reasons = []
        if not finite(stock.get("price")) or stock["price"] <= 0:
            reasons.append("Missing or invalid price")
        if not finite(ratio):
            reasons.append(stock["scenarios"].get("reason") or "Missing asymmetry inputs")
        elif ratio < config.MIN_ASYMMETRY:
            reasons.append(f"Asymmetry {ratio:.2f}× is below {config.MIN_ASYMMETRY}×")
        if stock["final_score"] <= 0:
            reasons.append("Non-positive final score")
        if stock["ticker"] == etf:
            reasons.append("Selected index ETF is not a satellite stock")
        if reasons:
            screened.append({
                "ticker": stock["ticker"], "reason": "; ".join(reasons),
                "fetch_errors": stock.get("fetch_errors", []),
            })
        else:
            candidates.append(stock)
    candidates.sort(key=lambda stock: (-stock["final_score"], stock["ticker"]))
    selected = candidates[:n_stocks]
    for stock in candidates[n_stocks:]:
        screened.append({
            "ticker": stock["ticker"], "reason": "Eligible, but below the selected top-N cutoff",
            "fetch_errors": stock.get("fetch_errors", []),
        })
    targets = constrained_targets(
        selected, amount * (1 - core_pct), amount * stock_cap, amount * sector_cap
    )
    holdings = []
    notes = []

    def add_holding(ticker, sector, price, target, stock=None):
        shares = math.floor(target / price)
        if shares < 1:
            return
        cost = shares * price
        holdings.append({
            "ticker": ticker, "sector": sector, "price": price,
            "target_amount": target, "amount": cost, "shares": shares,
            "weight": cost / amount,
            "final_score": stock["final_score"] if stock else None,
            "asymmetry_ratio": stock["scenarios"]["asymmetry_ratio"] if stock else None,
            "eps_cagr": stock["metrics"].get("eps_cagr") if stock else None,
            "kind": "stock" if stock else "core",
        })

    if finite(etf_price) and etf_price > 0:
        add_holding(etf, "Index core", etf_price, amount * core_pct)
    elif core_pct > 0:
        notes.append(f"{etf} price unavailable: the core budget remains cash.")

    for stock in selected:
        target = targets[stock["ticker"]]
        add_holding(stock["ticker"], stock["sector"], stock["price"], target, stock)
        if target < stock["price"]:
            screened.append({
                "ticker": stock["ticker"],
                "reason": "Selected, but caps/budget cannot fund one whole share",
                "fetch_errors": stock.get("fetch_errors", []),
            })
    invested = sum(holding["amount"] for holding in holdings)
    stock_holdings = [holding for holding in holdings if holding["kind"] == "stock"]
    if len(stock_holdings) < n_stocks:
        notes.append(
            f"Only {len(stock_holdings)} funded satellite holdings; no constraints were relaxed."
        )
    unused_capacity = amount * (1 - core_pct) - sum(targets.values())
    if unused_capacity > 0.01:
        notes.append(f"${unused_capacity:,.2f} of satellite budget has no feasible capped capacity.")

    def weighted(key):
        usable = [holding for holding in stock_holdings if finite(holding.get(key))]
        denominator = sum(holding["amount"] for holding in usable)
        return (
            sum(holding["amount"] * holding[key] for holding in usable) / denominator
            if denominator else None
        )

    return {
        "amount": amount, "core_pct": core_pct, "etf": etf,
        "holdings": holdings, "invested": invested, "cash": max(0.0, amount - invested),
        "weighted_asymmetry": weighted("asymmetry_ratio"),
        "weighted_eps_cagr": weighted("eps_cagr"),
        "screened": screened, "notes": notes,
        "stock_cap": stock_cap, "sector_cap": sector_cap, "requested_stocks": n_stocks,
    }
