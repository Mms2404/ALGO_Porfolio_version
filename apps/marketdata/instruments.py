"""Angel instrument reference data: scrip master (cached), filtering, token maps.

The scrip master (OpenAPIScripMaster.json) is large. The old code re-downloaded
it on EVERY filter call; here it is fetched once and cached for the trading day
(in-memory, thread-safe).
"""

from __future__ import annotations

import gzip
import json
import logging
import time
import threading
from datetime import date

import requests

logger = logging.getLogger(__name__)

SCRIP_MASTER_URL = (
    "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"
)

# --- index / stock reference ---
INDEX_TOKENS = {
    "NIFTY":     "99926000",   # NSE
    "BANKNIFTY": "99926009",   # NSE
    "FINNIFTY":  "99926037",   # NSE
    "SENSEX":    "99919000",          # BSE
}

# Which cash-segment exchange each index lives on (for LTP REST calls)
INDEX_EXCHANGE = {
    "NIFTY":     "NSE",
    "BANKNIFTY": "NSE",
    "FINNIFTY":  "NSE",
    "SENSEX":    "BSE",
}

# Which F&O exchange each index's options trade on
INDEX_FNO_EXCHANGE = {
    "NIFTY":     "NFO",
    "BANKNIFTY": "NFO",
    "FINNIFTY":  "NFO",
    "SENSEX":    "BFO",
}

# WebSocket exchange_type codes
WS_EXCHANGE_TYPE = {
    "NFO": 2,
    "BFO": 4,
}

INDEX_STRIKE_STEPS = {
    "NIFTY":     50,
    "BANKNIFTY": 100,
    "FINNIFTY":  50,
    "SENSEX":    100,
}

STOCK_TOKENS = {
    # Financial Services
    "HDFCBANK": "1333", "ICICIBANK": "4963", "SBIN": "3045", "AXISBANK": "5900",
    "BAJFINANCE": "317", "BAJAJFINSV": "16675", "SHRIRAMFIN": "4306",
    "KOTAKBANK": "1922", "SBILIFE": "21808", "HDFCLIFE": "467",
    # IT
    "TCS": "11536", "INFY": "1594", "HCLTECH": "7229", "TECHM": "13538", "WIPRO": "3787",
    # Oil & Energy
    "RELIANCE": "2885", "ONGC": "2475", "POWERGRID": "14977", "NTPC": "11630",
    # Automobile
    "M&M": "519", "MARUTI": "10999", "TATAMOTORS": "3456",
    "BAJAJ-AUTO": "16669", "EICHERMOT": "910", "HEROMOTOCO": "1348",
    # Consumer Goods
    "HINDUNILVR": "1394", "ITC": "1660", "NESTLEIND": "17963", "TATACONSUM": "3432",
    # Healthcare
    "SUNPHARMA": "3351", "CIPLA": "694", "DRREDDY": "881",
    "APOLLOHOSP": "157", "DIVISLAB": "10940",
    # Metals & Mining
    "TATASTEEL": "3499", "JSWSTEEL": "11723", "HINDALCO": "1363", "COALINDIA": "20374",
    # Construction & Infrastructure
    "LT": "11483", "ULTRACEMCO": "11532", "GRASIM": "1232", "ADANIPORTS": "15083",
    # Services & Others
    "BHARTIARTL": "10604", "ADANIENT": "25", "BEL": "383",
    "TITAN": "3506", "ETERNAL": "5097", "JIOFIN": "543257",
}

STOCK_STRIKE_STEPS = {
    "HDFCBANK": 20, "ICICIBANK": 20, "SBIN": 10, "AXISBANK": 20,
    "BAJFINANCE": 100, "BAJAJFINSV": 50, "SHRIRAMFIN": 50,
    "KOTAKBANK": 20, "SBILIFE": 20, "HDFCLIFE": 20,
    "TCS": 20, "INFY": 20, "HCLTECH": 20, "TECHM": 20, "WIPRO": 10,
    "RELIANCE": 20, "ONGC": 5, "POWERGRID": 5, "NTPC": 5,
    "M&M": 20, "MARUTI": 100, "TATAMOTORS": 10,
    "BAJAJ-AUTO": 50, "EICHERMOT": 100, "HEROMOTOCO": 50,
    "HINDUNILVR": 20, "ITC": 5, "NESTLEIND": 100, "TATACONSUM": 20,
    "SUNPHARMA": 20, "CIPLA": 20, "DRREDDY": 50,
    "APOLLOHOSP": 50, "DIVISLAB": 100,
    "TATASTEEL": 5, "JSWSTEEL": 20, "HINDALCO": 10, "COALINDIA": 10,
    "LT": 50, "ULTRACEMCO": 50, "GRASIM": 50, "ADANIPORTS": 20,
    "BHARTIARTL": 20, "ADANIENT": 20, "BEL": 5,
    "TITAN": 50, "ETERNAL": 5, "JIOFIN": 5,
}


# --- scrip master (cached for the day) ---
_scrip_lock = threading.Lock()
_scrip_cache: dict = {"date": None, "data": None}

# In-memory cache above only survives within ONE process AND ONE container.
# Celery's prefork pool runs each worker slot (--concurrency=N) as a
# SEPARATE OS process, so with concurrency=5 there can be up to 5
# independent in-memory caches. On top of that, Railway (and most
# container hosts) give every redeploy/restart a BRAND NEW container --
# nothing written to local disk survives that, regardless of path. So the
# shared, persistent layer lives in Supabase Storage instead: one fixed
# object, always overwritten (upsert) with today's date embedded in its
# content. Redeploying 10 times in one day still reads the same cached
# object; a new calendar day naturally invalidates it via the date check.
_STORAGE_BUCKET = "algo-cache"
_SCRIP_MASTER_STORAGE_PATH = "scrip_master.json"


def _read_storage_cache() -> list | None:
    from core.supabase.client import get_client
    logger.info("_read_storage_cache: calling Supabase Storage .download()")
    try:
        raw = get_client().storage.from_(_STORAGE_BUCKET).download(_SCRIP_MASTER_STORAGE_PATH)
        logger.info("_read_storage_cache: .download() returned %d bytes", len(raw))
    except Exception as exc:
        logger.info("_read_storage_cache: .download() raised (expected on cache miss): %s", exc)
        return None  # not found yet, or bucket/network issue -- fall through to download

    try:
        decompressed = gzip.decompress(raw)
        cached = json.loads(decompressed)
    except Exception as exc:
        logger.warning("Scrip master Storage cache unreadable (ignoring, will re-download): %s", exc)
        return None

    if cached.get("date") != date.today().isoformat():
        return None  # yesterday's (or older) cache -- stale, re-download
    return cached.get("data")


def _write_storage_cache(data: list) -> None:
    from core.supabase.client import get_client
    try:
        raw = json.dumps({"date": date.today().isoformat(), "data": data}).encode("utf-8")
        payload = gzip.compress(raw, compresslevel=6)
        get_client().storage.from_(_STORAGE_BUCKET).upload(
            _SCRIP_MASTER_STORAGE_PATH, payload,
            file_options={"content-type": "application/gzip", "upsert": "true"},
        )
    except Exception as exc:
        logger.warning("Scrip master Storage cache write failed (non-fatal): %s", exc)


def fetch_scrip_master(force: bool = False) -> list:
    """Return the full scrip master, cached per calendar day.

    Two cache layers: an in-memory one (fastest, but private to this
    process/container) backed by Supabase Storage (shared across every
    Celery worker process AND survives redeploys/restarts, since it's
    not tied to any one container's filesystem). Only actually hits
    Angel's servers when both are missing/stale.

    Requires a private Storage bucket named "algo-cache" to already
    exist in Supabase (create once via the dashboard: Storage -> New
    bucket -> name "algo-cache" -> Private). The service-role key this
    project already uses bypasses bucket privacy restrictions, so this
    works with zero additional policy setup once the bucket exists.
    """
    today = date.today()
    logger.info("fetch_scrip_master: waiting for lock (force=%s)", force)
    with _scrip_lock:
        logger.info("fetch_scrip_master: lock acquired")
        if not force and _scrip_cache["date"] == today and _scrip_cache["data"] is not None:
            logger.info("fetch_scrip_master: in-memory cache hit")
            return _scrip_cache["data"]

        if not force:
            logger.info("fetch_scrip_master: in-memory cache miss, checking Supabase Storage")
            storage_data = _read_storage_cache()
            logger.info("fetch_scrip_master: Storage check done, hit=%s", storage_data is not None)
            if storage_data is not None:
                _scrip_cache["date"] = today
                _scrip_cache["data"] = storage_data
                logger.info("Scrip master loaded from Supabase Storage cache: %d instruments", len(storage_data))
                return storage_data

        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                logger.info("Downloading Angel scrip master (attempt %d/%d)...", attempt, max_retries)
                resp = requests.get(SCRIP_MASTER_URL, timeout=120, stream=True)
                resp.raise_for_status()
                data = resp.json()
                _scrip_cache["date"] = today
                _scrip_cache["data"] = data
                _write_storage_cache(data)
                logger.info("Scrip master cached: %d instruments", len(data))
                return data
            except Exception as exc:
                logger.warning("Scrip master download failed (attempt %d/%d): %s", attempt, max_retries, exc)
                if attempt < max_retries:
                    time.sleep(5 * attempt)   # 5s, 10s back-off
                else:
                    raise RuntimeError(f"Scrip master download failed after {max_retries} attempts: {exc}") from exc


def filter_instruments(name=None, expiry=None, instrumenttype=None, *, name_prefix=False) -> list:
    """Filter NFO instruments."""
    return _filter_by_exchange("NFO", name, expiry, instrumenttype, name_prefix=name_prefix)


def filter_bfo_instruments(name=None, expiry=None, instrumenttype=None, *, name_prefix=False) -> list:
    """Filter BFO (BSE F&O) instruments — used for SENSEX options."""
    return _filter_by_exchange("BFO", name, expiry, instrumenttype, name_prefix=name_prefix)



def _filter_by_exchange(exchange: str, name=None, expiry=None, instrumenttype=None, *, name_prefix=False) -> list:
    out = []
    for item in fetch_scrip_master():
        if item.get("exch_seg") != exchange:
            continue
        item_name = item.get("name", "")
        if name:
            if name_prefix and not item_name.startswith(name):
                continue
            if not name_prefix and item_name != name:
                continue
        if expiry and item.get("expiry") != expiry:
            continue
        if instrumenttype and item.get("instrumenttype") != instrumenttype:
            continue
        out.append(item)
    return out


# --- index options (expiry REQUIRED — no stale default) ---
def get_index_token(index_name: str) -> str | None:
    return INDEX_TOKENS.get(index_name.upper())


def get_index_options(index_name: str, expiry: str) -> list:
    return filter_instruments(name=index_name.upper(), expiry=expiry, instrumenttype="OPTIDX")


def nifty_tokens(expiry: str) -> list:
    return get_index_options("NIFTY", expiry)


def bank_tokens(expiry: str) -> list:
    return get_index_options("BANKNIFTY", expiry)


def fin_tokens(expiry: str) -> list:
    return get_index_options("FINNIFTY", expiry)


def get_options_by_index(index_name: str, expiry: str) -> list:
    """Return options for index — routes to BFO for SENSEX, NFO for everything else."""
    exchange = INDEX_FNO_EXCHANGE.get(index_name.upper(), "NFO")
    if exchange == "BFO":
        return filter_bfo_instruments(name=index_name.upper(), expiry=expiry, instrumenttype="OPTIDX")
    return get_index_options(index_name, expiry)


def build_subscriptions(token_list, mode: int = 1) -> list:
    """Build an Angel websocket subscription list (NFO) from instrument dicts."""
    tokens = [item["token"] for item in token_list]
    return [{"exchange_type": 2, "tokens": tokens, "mode": mode}]


def build_token_meta(token_list) -> dict:
    """token(int) -> {symbol, name, expiry, strike(rupees), type}.

    Replaces the old global TOKEN_META + load_token_meta() (which only ever held
    NIFTY and was mutable global state). Build per-strategy from whatever that
    strategy subscribes to.
    """
    meta = {}
    for item in token_list:
        symbol = item["symbol"]
        meta[int(item["token"])] = {
            "symbol": symbol,
            "name": item["name"],
            "expiry": item["expiry"],
            "strike": float(item["strike"]) / 100,  # paise -> rupees
            "type": "CE" if symbol.endswith("CE") else "PE",
        }
    return meta


# --- stock F&O reference ---
def get_stock_token(stock_name: str) -> str | None:
    return STOCK_TOKENS.get(stock_name.upper())


def get_stock_strike_step(stock_name: str) -> int:
    return STOCK_STRIKE_STEPS.get(stock_name.upper(), 50)


def filter_stock_instruments(name: str, expiry: str = None, instrumenttype: str = None) -> list:
    return filter_instruments(
        name=name.upper() if name else None, expiry=expiry, instrumenttype=instrumenttype
    )


def get_stock_future(stock_name: str, expiry: str) -> dict | None:
    futures = filter_stock_instruments(name=stock_name, expiry=expiry, instrumenttype="FUTSTK")
    return futures[0] if futures else None


def get_stock_options(stock_name: str, expiry: str) -> list:
    return filter_stock_instruments(name=stock_name, expiry=expiry, instrumenttype="OPTSTK")