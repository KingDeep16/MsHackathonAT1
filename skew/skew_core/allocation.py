"""Turn scored candidates into a dollar allocation. Deterministic: same inputs, same portfolio."""

import math


def asymmetry_bonus(ratio):
    """0-10 points; maxes out at a 5x asymmetry ratio."""
    return 10 * min(max(ratio, 0), 5) / 5


def final_score(candidate):
    return candidate["quant_score"] + candidate.get("ai_adjustment", 0) + asymmetry_bonus(candidate["asymmetry"])


def screen(candidates, min_asymmetry):
    """Split candidates into eligible and excluded (with a reason for each exclusion)."""
    eligible, excluded = [], []
    for c in candidates:
        if not c.get("price"):
            excluded.append({**c, "reason": "No current price"})
        elif c.get("asymmetry") is None:
            excluded.append({**c, "reason": "Not enough data to model scenarios (needs positive EPS and P/E)"})
        elif c["asymmetry"] < min_asymmetry:
            excluded.append({**c, "reason": f"Asymmetry {c['asymmetry']:.1f}x is below the {min_asymmetry:.1f}x minimum"})
        else:
            eligible.append({**c, "final_score": round(final_score(c), 1)})
    eligible.sort(key=lambda c: c["final_score"], reverse=True)
    return eligible, excluded


def cap_weights(scores, sectors, sleeve, stock_cap, sector_cap):
    """Split `sleeve` dollars in proportion to `scores`, respecting per-stock and per-sector dollar caps.

    Money that can't be placed under the caps is returned as unallocated rather than forced in.
    """
    n = len(scores)
    dollars = [0.0] * n
    remaining = sleeve
    active = set(range(n))
    for _ in range(4 * n + 4):
        if remaining < 0.01 or not active:
            break
        total = sum(max(scores[i], 0.01) for i in active)
        for i in active:
            dollars[i] += remaining * max(scores[i], 0.01) / total
        remaining = 0.0
        # Trim stocks over their cap
        for i in list(active):
            if dollars[i] > stock_cap:
                remaining += dollars[i] - stock_cap
                dollars[i] = stock_cap
                active.discard(i)
        # Trim sectors over their cap, proportionally within the sector
        for sector in set(sectors):
            members = [i for i in range(n) if sectors[i] == sector]
            held = sum(dollars[i] for i in members)
            if held > sector_cap + 1e-9:
                factor = sector_cap / held
                for i in members:
                    remaining += dollars[i] * (1 - factor)
                    dollars[i] *= factor
                    active.discard(i)
    return dollars, remaining


def allocate(candidates, profile, etf_price):
    amount = float(profile["amount"])
    eligible, excluded = screen(candidates, profile["min_asymmetry"])
    picks = eligible[: int(profile["num_stocks"])]
    not_picked = [{**c, "reason": f"Ranked #{rank} — outside the top {len(picks)}"}
                  for rank, c in enumerate(eligible[len(picks):], start=len(picks) + 1)]

    sleeve = amount * (1 - profile["core_pct"])
    dollars, unplaced = cap_weights(
        [c["final_score"] for c in picks], [c["sector"] for c in picks], sleeve,
        amount * profile["max_stock_pct"], amount * profile["max_sector_pct"],
    )

    holdings = []
    for c, target in zip(picks, dollars):
        shares = math.floor(target / c["price"])
        holdings.append({
            "ticker": c["ticker"], "name": c["name"], "sector": c["sector"], "kind": "stock",
            "price": c["price"], "shares": shares, "amount": round(shares * c["price"], 2),
            "target": round(target, 2), "final_score": c["final_score"], "asymmetry": c["asymmetry"],
        })

    # Index core absorbs anything the caps couldn't place, so cash drag stays small
    core_target = amount * profile["core_pct"] + unplaced
    core_shares = math.floor(core_target / etf_price) if etf_price else 0
    holdings.insert(0, {
        "ticker": profile["etf"], "name": profile.get("etf_name", profile["etf"]), "sector": "Index core",
        "kind": "etf", "price": etf_price, "shares": core_shares,
        "amount": round(core_shares * etf_price, 2) if etf_price else 0.0, "target": round(core_target, 2),
    })

    invested = round(sum(h["amount"] for h in holdings), 2)
    for h in holdings:
        h["weight"] = h["amount"] / amount
    stocks = [h for h in holdings if h["kind"] == "stock"]
    stock_total = sum(h["amount"] for h in stocks) or 1
    return {
        "holdings": holdings,
        "invested": invested,
        "cash": round(amount - invested, 2),
        "excluded": excluded + not_picked,
        "weighted_asymmetry": round(sum(h["asymmetry"] * h["amount"] for h in stocks) / stock_total, 2) if stocks else None,
    }
