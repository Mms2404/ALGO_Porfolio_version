"""Angel REST 'quote' market-data source (LTP).

Used where the websocket can't help -- notably index spot (NSE index can't be
mixed with NFO options in one websocket subscription) and narrow strategies that
need only a few LTPs (quote API: up to 50 symbols/request, 1 req/sec).

These RAISE on failure; the old versions returned 0.0 silently, which would feed
a bogus spot of 0 into ATM math.
"""

from __future__ import annotations

import logging

import requests

from apps.marketdata.instruments import INDEX_TOKENS, INDEX_EXCHANGE, STOCK_TOKENS
from core.angel.headers import get_default_headers

logger = logging.getLogger(__name__)

QUOTE_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/market/v1/quote/"


def _ltp_quote(api_key: str, jwt_token: str, exchange_tokens: dict) -> list:
    headers = get_default_headers(api_key=api_key, include_jwt=jwt_token)
    payload = {"mode": "LTP", "exchangeTokens": exchange_tokens}
    resp = requests.post(QUOTE_URL, json=payload, headers=headers, timeout=10)
    data = resp.json()
    # Angel uses both "status" and "success" across endpoints
    ok = data.get("status") or data.get("success")
    if not ok:
        msg = data.get("message") or data.get("Message")
        code = data.get("errorCode") or data.get("errorcode")
        raise RuntimeError(f"Quote error: {msg} ({code})")
    return data.get("data", {}).get("fetched", [])


def get_index_ltp(index_name: str, api_key: str, jwt_token: str) -> float:
    idx = index_name.upper()

    # SENSEX: BSE token "1" returns 0 from Angel LTP API.
    # Use nearest BFO SENSEX futures LTP as spot proxy (futures ≈ spot).
    if idx == "SENSEX":
        return _get_sensex_ltp_from_futures(api_key, jwt_token)

    token = INDEX_TOKENS.get(idx)
    if not token:
        raise ValueError(f"Unknown index: {index_name}. Valid: {list(INDEX_TOKENS)}")
    exchange = INDEX_EXCHANGE.get(idx, "NSE")
    fetched = _ltp_quote(api_key, jwt_token, {exchange: [token]})
    if not fetched:
        raise RuntimeError(f"No LTP returned for {index_name}")
    return float(fetched[0].get("ltp", 0))


def _get_sensex_ltp_from_futures(api_key: str, jwt_token: str) -> float:
    """Get SENSEX spot via nearest BFO SENSEX futures LTP (reliable proxy)."""
    from apps.marketdata.instruments import filter_bfo_instruments
    from apps.marketdata.expiry import get_nearest_expiry

    expiry = get_nearest_expiry("SENSEX", "FUTIDX")
    if not expiry:
        raise RuntimeError("No SENSEX futures expiry found in scrip master")

    futures = filter_bfo_instruments(name="SENSEX", expiry=expiry, instrumenttype="FUTIDX")
    if not futures:
        raise RuntimeError(f"No SENSEX futures found for expiry {expiry}")

    token = futures[0]["token"]
    fetched = _ltp_quote(api_key, jwt_token, {"BFO": [token]})
    if not fetched:
        raise RuntimeError("No LTP returned for SENSEX futures")

    ltp = float(fetched[0].get("ltp", 0))
    if ltp <= 0:
        raise RuntimeError(f"SENSEX futures LTP is zero — token={token} expiry={expiry}")

    logger.info("SENSEX spot via BFO futures: %.2f (token=%s expiry=%s)", ltp, token, expiry)
    return ltp


def get_multiple_index_ltp(api_key: str, jwt_token: str) -> dict:
    fetched = _ltp_quote(api_key, jwt_token, {"NSE": list(INDEX_TOKENS.values())})
    token_to_index = {v: k for k, v in INDEX_TOKENS.items()}
    result = {name: 0.0 for name in INDEX_TOKENS}
    for item in fetched:
        name = token_to_index.get(item.get("symbolToken"))
        if name:
            result[name] = float(item.get("ltp", 0))
    return result


def get_stock_ltp(stock_name: str, api_key: str, jwt_token: str) -> float:
    token = STOCK_TOKENS.get(stock_name.upper())
    if not token:
        raise ValueError(f"Unknown stock: {stock_name}. Valid: {list(STOCK_TOKENS)}")
    fetched = _ltp_quote(api_key, jwt_token, {"NSE": [token]})
    if not fetched:
        raise RuntimeError(f"No LTP returned for {stock_name}")
    return float(fetched[0].get("ltp", 0))


def get_ltp_by_token(exchange: str, token: str, api_key: str, jwt_token: str) -> float:
    """LTP for a single already-known instrument token (e.g. a specific
    option contract) on a given exchange segment ('NFO' or 'BFO')."""
    fetched = _ltp_quote(api_key, jwt_token, {exchange: [token]})
    if not fetched:
        raise RuntimeError(f"No LTP returned for token {token} on {exchange}")
    return float(fetched[0].get("ltp", 0)) 