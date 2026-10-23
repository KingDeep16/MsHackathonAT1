import os

AZURE_ENDPOINT = "https://app-uoaihack2ix6ak.azurewebsites.net"
API_VERSION = "2024-12-01-preview"
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-6.1-sol")

SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")

# US-listed companies that file 10-Ks, so SEC EDGAR has their full fundamentals
DEFAULT_UNIVERSE = [
    "MSFT", "GOOGL", "AMZN", "NVDA", "META", "AVGO", "AMD", "ADBE",
    "CRM", "NOW", "INTU", "CDNS", "MA", "AXP", "SPGI", "MCO",
    "COST", "LLY", "UNH", "ISRG", "BKNG", "NFLX", "ANET", "CMG",
]

DEFAULT_PROFILE = {
    "amount": 100_000,
    "core_pct": 0.30,
    "etf": "VOO",
    "num_stocks": 8,
    "max_stock_pct": 0.12,
    "max_sector_pct": 0.35,
    "min_asymmetry": 1.5,
}

ETF_CHOICES = {
    "VOO": "Vanguard S&P 500 ETF",
    "VT": "Vanguard Total World Stock ETF",
    "QQQ": "Invesco QQQ (Nasdaq-100)",
}

HORIZON_YEARS = 5
