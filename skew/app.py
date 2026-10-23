"""Skew API. The S&P 500 dataset is static (public/data); the server only does what needs secrets or
live data: AI analysis, live prices for the final picks, and the client memo. Each endpoint is one
short step so it fits in a serverless function (e.g. Vercel).

Run locally:  uvicorn app:app --reload --port 8000
"""

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from skew_core import ai, allocation, config, sources

app = FastAPI(title="Skew")


class Profile(BaseModel):
    amount: float = config.DEFAULT_PROFILE["amount"]
    core_pct: float = config.DEFAULT_PROFILE["core_pct"]
    etf: str = config.DEFAULT_PROFILE["etf"]
    num_stocks: int = config.DEFAULT_PROFILE["num_stocks"]
    max_stock_pct: float = config.DEFAULT_PROFILE["max_stock_pct"]
    max_sector_pct: float = config.DEFAULT_PROFILE["max_sector_pct"]
    min_asymmetry: float = config.DEFAULT_PROFILE["min_asymmetry"]


class Candidate(BaseModel):
    ticker: str
    name: str
    sector: str
    price: float | None = None
    quant_score: float
    asymmetry: float | None = None
    ai_adjustment: int = 0


class PortfolioRequest(BaseModel):
    profile: Profile
    candidates: list[Candidate]


class MemoRequest(BaseModel):
    profile: Profile
    allocation: dict
    analyses: dict


@app.get("/api/health")
def health():
    return {"model": config.LLM_MODEL, "ai": bool(os.getenv("OPENAI_API_KEY")), "etfs": config.ETF_CHOICES}


@app.post("/api/analyze")
def analyze(detail: dict):
    try:
        return ai.analyse_company(detail)
    except Exception as error:
        raise HTTPException(502, str(error))


def _live_price(ticker):
    try:
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            return sources.nasdaq_price(client, ticker)["price"]
    except Exception:
        return None


@app.post("/api/portfolio")
def portfolio(request: PortfolioRequest):
    profile = request.profile.model_dump()
    profile["etf_name"] = config.ETF_CHOICES.get(profile["etf"], profile["etf"])
    candidates = [c.model_dump() for c in request.candidates]

    # Refresh prices for the stocks likely to be picked (plus the ETF); fall back to the snapshot price
    likely = sorted((c for c in candidates if c["asymmetry"] and c["asymmetry"] >= profile["min_asymmetry"]),
                    key=allocation.final_score, reverse=True)[: profile["num_stocks"] * 2]
    with ThreadPoolExecutor(8) as pool:
        prices = dict(zip([c["ticker"] for c in likely] + [profile["etf"]],
                          pool.map(_live_price, [c["ticker"] for c in likely] + [profile["etf"]])))
    live = 0
    for c in candidates:
        if prices.get(c["ticker"]):
            c["price"] = prices[c["ticker"]]
            live += 1
    if not prices.get(profile["etf"]):
        raise HTTPException(502, f"Could not fetch a live price for {profile['etf']}.")

    result = allocation.allocate(candidates, profile, prices[profile["etf"]])
    result["live_prices"] = live
    return result


@app.post("/api/memo")
def memo(request: MemoRequest):
    try:
        return ai.write_memo(request.profile.model_dump(), request.allocation, request.analyses)
    except Exception as error:
        raise HTTPException(502, str(error))


# On Vercel, public/ is served by the CDN; locally FastAPI serves it
app.mount("/", StaticFiles(directory=Path(__file__).parent / "public", html=True, check_dir=False), name="public")
