import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

APP_NAME = "Skew"
CLIENT_NAME = "Patient Asymmetric Investor"
CLIENT_ARCHETYPE = "Long-horizon fundamentals client"
CLIENT_MANDATE = (
    "Allocate US$100,000 into a diversified index core plus high-conviction "
    "satellites where realistic five-year upside meaningfully exceeds downside."
)
CLIENT_CONSTRAINTS = [
    "Horizon: 5+ years",
    "Style: fundamentals first, no day-trading",
    "Structure: core–satellite",
    "Screen: asymmetry ratio ≥ 1.5×",
    "Caps: per-stock and per-sector limits on satellites",
]

AZURE_ENDPOINT = os.getenv(
    "AZURE_OPENAI_ENDPOINT",
    os.getenv("AZURE_ENDPOINT", "https://app-uoaihack2ix6ak.azurewebsites.net"),
)
API_VERSION = os.getenv("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-6.1-sol")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
SEC_USER_AGENT = os.getenv("SEC_USER_AGENT", "")

DEFAULT_UNIVERSE = [
    "MSFT", "GOOGL", "AMZN", "NVDA", "ASML", "V", "MA", "COST",
    "LLY", "NVO", "UNH", "ADBE", "CRM", "NOW", "INTU", "ISRG",
    "SPGI", "MCO", "AXP", "BKNG", "TSM", "AVGO", "META", "AMD", "NFLX",
]
DEFAULT_AMOUNT = 100_000
DEFAULT_CORE = 0.30
DEFAULT_ETF = "VOO"
ETF_CHOICES = ["VOO", "VT", "QQQ"]
DEFAULT_STOCKS = 8
DEFAULT_STOCK_CAP = 0.15
DEFAULT_SECTOR_CAP = 0.35
MIN_ASYMMETRY = 1.5
DISPLAY_ASYMMETRY_CAP = 10.0
SCENARIO_YEARS = 5
AI_TOP_N = 12
MAX_WORKERS = 5
CACHE_HOURS = 24
SEC_REQUEST_INTERVAL = 0.16
HTTP_TIMEOUT = 20
LLM_TIMEOUT = 75
NEWS_LIMIT = 10
RELEASE_LIMIT = 12_000
DATA_DIR = Path("data")
CACHE_DIR = DATA_DIR / "cache"
AI_CACHE_DIR = DATA_DIR / "ai_cache"
LAST_RUN_PATH = DATA_DIR / "last_run.json"
RUN_SCHEMA_VERSION = 2
MACRO_SYMBOL = "^TNX"

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS_BASE = "https://data.sec.gov/api/xbrl/companyfacts"
SEC_SUBMISSIONS_BASE = "https://data.sec.gov/submissions"
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

FINANCIAL_TAGS = {
    "revenue": [
        "Revenues",
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "SalesRevenueNet",
    ],
    "eps": ["EarningsPerShareDiluted"],
    "operating_income": ["OperatingIncomeLoss"],
    "net_income": ["NetIncomeLoss"],
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
    "debt": ["LongTermDebt", "LongTermDebtNoncurrent"],
    "equity": ["StockholdersEquity"],
}
