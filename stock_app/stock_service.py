from datetime import datetime, timedelta
from pathlib import Path
from difflib import get_close_matches
import re
import time
from urllib.parse import urlencode
from collections import OrderedDict

import pandas as pd
import requests
import yfinance as yf


# Map common company names to tickers
STOCK_MAP = {
    "apple": "AAPL",
    "google": "GOOG",
    "microsoft": "MSFT",
    "amazon": "AMZN",
    "tesla": "TSLA",
    "nvidia": "NVDA",
    "tsmc": "TSM",
    "meta": "META",
    "netflix": "NFLX",
    "intel": "INTC",
    "amd": "AMD",
    "asml": "ASML",
}

BASE_DIR = Path(__file__).resolve().parent
YAHOO_SEARCH_URL = "https://query2.finance.yahoo.com/v1/finance/search"
YAHOO_AUTOC_URL = "https://autoc.finance.yahoo.com/autoc"
YAHOO_SCREENER_URL = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9.\-^=]{1,10}$")
HISTORY_CACHE: "OrderedDict[tuple[str, int, str, str], tuple[float, pd.DataFrame, str]]" = OrderedDict()
HISTORY_CACHE_TTL = 30  # seconds
HISTORY_CACHE_MAX = 12
FULL_HISTORY_CACHE: "OrderedDict[str, tuple[float, pd.DataFrame]]" = OrderedDict()
FULL_HISTORY_TTL = 30  # seconds
FULL_HISTORY_MAX = 12


def find_ticker(user_input: str) -> str:
    user_input = user_input.strip()
    if not user_input:
        raise ValueError("Stock not found. Try a valid company name or ticker.")

    lower = user_input.lower()
    if user_input.upper() in STOCK_MAP.values():
        return user_input.upper()
    matches = get_close_matches(lower, STOCK_MAP.keys(), n=1, cutoff=0.5)
    if matches:
        return STOCK_MAP[matches[0]]

    # If the input already looks like a ticker, return it directly.
    if SYMBOL_PATTERN.match(user_input):
        return user_input.upper()

    # Fallback: search the wider market by company name.
    matches = search_symbols(user_input, limit=5)
    if matches:
        return matches[0]["symbol"]
    raise ValueError("Stock not found. Try a valid company name or ticker.")


def list_symbols() -> list[dict]:
    """Return a list of symbols for the UI picker (most actives with fallback)."""
    try:
        most_active = fetch_most_active(count=25)
        if most_active:
            return most_active
    except Exception:
        pass

    seen = set()
    items = []
    for name, sym in STOCK_MAP.items():
        if sym in seen:
            continue
        seen.add(sym)
        items.append({"symbol": sym, "name": name.title()})
    # keep consistent order
    return sorted(items, key=lambda x: x["symbol"])


def fetch_most_active(count: int = 25) -> list[dict]:
    """Fetch most active stocks from Yahoo Finance screener."""
    headers = {"User-Agent": "Mozilla/5.0 (stock-viewer)"}
    params = {
        "scrIds": "most_actives",
        "count": count,
        "lang": "en-US",
        "region": "US",
        "formatted": "false",
    }
    resp = requests.get(YAHOO_SCREENER_URL, headers=headers, params=params, timeout=6)
    resp.raise_for_status()
    data = resp.json() or {}
    quotes = data.get("finance", {}).get("result", [])
    if quotes:
        quotes = quotes[0].get("quotes", [])
    results = []
    for q in quotes:
        symbol = q.get("symbol")
        name = q.get("shortName") or q.get("longName") or symbol
        if symbol:
            results.append({"symbol": symbol.upper(), "name": name})
    # De-duplicate while preserving order.
    seen = set()
    unique = []
    for row in results:
        sym = row["symbol"]
        if sym in seen:
            continue
        seen.add(sym)
        unique.append(row)
    return unique[:count]


def _dedupe(rows: list[dict], limit: int) -> list[dict]:
    seen = set()
    unique = []
    for row in rows:
        sym = row["symbol"]
        if sym in seen:
            continue
        seen.add(sym)
        unique.append(row)
        if len(unique) >= limit:
            break
    return unique


def _search_primary(query: str, limit: int, headers: dict) -> list[dict]:
    params = {"q": query, "quotesCount": limit, "newsCount": 0, "listsCount": 0}
    resp = requests.get(f"{YAHOO_SEARCH_URL}?{urlencode(params)}", headers=headers, timeout=6)
    resp.raise_for_status()
    data = resp.json()
    rows = []
    for item in data.get("quotes", []):
        quote_type = (item.get("quoteType") or "").lower()
        if quote_type and quote_type not in {"equity", "etf", "mutualfund"}:
            continue
        rows.append(
            {
                "symbol": item.get("symbol"),
                "name": item.get("longname") or item.get("shortname") or item.get("symbol"),
            }
        )
    return rows


def _search_autoc(query: str, headers: dict) -> list[dict]:
    params = {"query": query, "region": "1", "lang": "en"}
    resp = requests.get(f"{YAHOO_AUTOC_URL}?{urlencode(params)}", headers=headers, timeout=6)
    resp.raise_for_status()
    data = resp.json() or {}
    rows = []
    for item in data.get("ResultSet", {}).get("Result", []):
        quote_type = (item.get("typeDisp") or "").lower()
        if quote_type and quote_type not in {"equity", "etf", "fund", "mutualfund"}:
            continue
        rows.append({"symbol": item.get("symbol"), "name": item.get("name")})
    return rows


def search_symbols(query: str, limit: int = 15) -> list[dict]:
    """Search Yahoo Finance for symbols matching the query with a local and autoc fallback."""
    query = query.strip()
    if not query:
        return []

    headers = {"User-Agent": "Mozilla/5.0 (stock-viewer)"}
    collected: list[dict] = []

    try:
        collected.extend(_search_primary(query, limit, headers))
    except Exception:
        collected = []

    if not collected:
        try:
            collected.extend(_search_autoc(query, headers))
        except Exception:
            collected = []

    if not collected:
        if SYMBOL_PATTERN.match(query):
            collected.append({"symbol": query.upper(), "name": query.upper()})
        fuzzy = get_close_matches(query.lower(), STOCK_MAP.keys(), n=5, cutoff=0.4)
        for name in fuzzy:
            collected.append({"symbol": STOCK_MAP[name], "name": name.title()})

    return _dedupe(collected, limit)


def get_stock_info(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.info or {}
    return {
        "symbol": ticker,
        "name": info.get("longName", ticker),
        "sector": info.get("sector", "N/A"),
        "industry": info.get("industry", "N/A"),
        "marketCap": info.get("marketCap"),
        "currentPrice": info.get("currentPrice"),
        "fiftyTwoWeekHigh": info.get("fiftyTwoWeekHigh"),
        "fiftyTwoWeekLow": info.get("fiftyTwoWeekLow"),
        "website": info.get("website"),
    }


SHORT_INTERVALS = {"1m", "2m", "5m", "15m", "30m", "1h", "1d"}
LONG_INTERVALS = {"1d", "5d", "1wk", "1mo", "3mo"}
YF_TIMEOUT = 6


def _choose_interval(range_key: str | None, requested: str | None) -> str:
    defaults = {
        "1d": "1m",
        "1w": "5m",
        "1m": "1d",
        "3m": "1d",
        "6m": "1d",
    }
    default = defaults.get(range_key or "", "1d")
    if not requested:
        return default
    if range_key in {"1d", "1w", "1m", "3m", "6m"}:
        return requested if requested in SHORT_INTERVALS else default
    return requested if requested in LONG_INTERVALS else default


def _choose_period(range_key: str | None, days: int, interval: str) -> dict:
    """Return kwargs for yf history: either period or start/end."""
    short_map = {
        "1d": "1d",
        "1w": "7d",
        "1m": "1mo",
        "3m": "3mo",
        "6m": "6mo",
    }
    period = short_map.get(range_key or "")
    if interval in SHORT_INTERVALS and period:
        return {"period": period}
    # fall back to start/end for long windows
    end = datetime.now()
    start = end - timedelta(days=days)
    return {"start": start, "end": end}


def get_history(
    ticker: str, days: int, range_key: str | None = None, interval: str | None = None
) -> tuple[pd.DataFrame, str]:
    """Return historical prices with flexible interval selection."""
    ticker_upper = ticker.upper()
    cache_key = (ticker_upper, days, range_key or "", interval or "")
    now = time.time()
    cached = HISTORY_CACHE.get(cache_key)
    if cached:
        ts, cached_df, cached_interval = cached
        if now - ts < HISTORY_CACHE_TTL:
            HISTORY_CACHE.move_to_end(cache_key)
            return cached_df.copy(), cached_interval
        HISTORY_CACHE.pop(cache_key, None)

    stock = yf.Ticker(ticker)
    eff_interval = _choose_interval(range_key, interval)
    kwargs = _choose_period(range_key, days, eff_interval)
    try:
        df = stock.history(interval=eff_interval, timeout=YF_TIMEOUT, **kwargs)
    except Exception:
        # Retry with default if the requested interval is unsupported for this range.
        eff_interval = _choose_interval(range_key, None)
        kwargs = _choose_period(range_key, days, eff_interval)
        df = stock.history(interval=eff_interval, timeout=YF_TIMEOUT, **kwargs)
    if df.empty:
        try:
            # Fall back to a longer daily window to avoid empty responses.
            df = _get_full_history(ticker).reset_index()
            eff_interval = "1d"
        except Exception as exc:
            raise ValueError("No data available for this ticker and range.") from exc
    df = df.reset_index()
    # Use datetime for intraday-like intervals, date for longer windows.
    if eff_interval in {"1m", "2m", "5m", "15m", "30m", "1h"} and "Datetime" in df.columns:
        df["Date"] = pd.to_datetime(df["Datetime"]).dt.strftime("%Y-%m-%d %H:%M")
    else:
        df["Date"] = pd.to_datetime(df["Date"]).dt.strftime("%Y-%m-%d")
    final_df = df[["Date", "Open", "High", "Low", "Close", "Volume"]]
    HISTORY_CACHE[cache_key] = (now, final_df.copy(), eff_interval)
    HISTORY_CACHE.move_to_end(cache_key)
    while len(HISTORY_CACHE) > HISTORY_CACHE_MAX:
        HISTORY_CACHE.popitem(last=False)
    return final_df, eff_interval


def _get_full_history(ticker: str) -> pd.DataFrame:
    key = ticker.upper()
    now = time.time()
    cached = FULL_HISTORY_CACHE.get(key)
    if cached:
        ts, df_cached = cached
        if now - ts < FULL_HISTORY_TTL:
            FULL_HISTORY_CACHE.move_to_end(key)
            return df_cached.copy()
        FULL_HISTORY_CACHE.pop(key, None)

    stock = yf.Ticker(key)
    df = stock.history(start="2012-01-01", end=datetime.now(), auto_adjust=True, timeout=YF_TIMEOUT)
    if df.empty:
        # Try Yahoo's full span if the bounded query returns nothing.
        df = stock.history(period="max", auto_adjust=True, timeout=YF_TIMEOUT)
    if df.empty:
        raise ValueError("No historical data available for this ticker.")

    FULL_HISTORY_CACHE[key] = (now, df.copy())
    FULL_HISTORY_CACHE.move_to_end(key)
    while len(FULL_HISTORY_CACHE) > FULL_HISTORY_MAX:
        FULL_HISTORY_CACHE.popitem(last=False)
    return df
