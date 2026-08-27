"""Angel SmartAPI order placement (LIVE broker call).

Pure broker adapter. It places a fully-specified order and returns Angel's
response. It deliberately does NOT:
  - resolve tokens   -> caller passes tradingsymbol + symboltoken
  - size positions   -> caller passes final `quantity` in units (lots * lot_size)
  - decide dry/live  -> the executor does
  - send notifications -> the executor does
"""

from __future__ import annotations

import aiohttp

from core.angel.headers import get_default_headers

PLACE_ORDER_URL = "https://apiconnect.angelone.in/rest/secure/angelbroking/order/v1/placeOrder"


async def place_order_angel(
    *,
    api_key: str,
    jwt: str,
    tradingsymbol: str,
    symboltoken: str,
    side: str,                 # "BUY" | "SELL"
    quantity: int,             # FINAL units (lots * lot_size) — computed by the caller
    exchange: str = "NFO",
    producttype: str = "CARRYFORWARD",
    ordertype: str = "MARKET",
    variety: str = "NORMAL",
    duration: str = "DAY",
    session: aiohttp.ClientSession | None = None,
) -> dict:
    """Place a single order and return the raw Angel response dict.

    Keyword-only on purpose: the old version multiplied qty * lot_size and
    callers passed the lot SIZE as the qty -> 75x oversize. Here `quantity` is
    already the final unit count, and positional misuse is impossible.
    """
    if quantity <= 0:
        raise ValueError(f"quantity must be > 0, got {quantity}")

    headers = get_default_headers(api_key=api_key, include_jwt=jwt)
    payload = {
        "exchange": exchange,
        "tradingsymbol": tradingsymbol,
        "symboltoken": str(symboltoken),
        "transactiontype": side,
        "variety": variety,
        "ordertype": ordertype,
        "producttype": producttype,
        "duration": duration,
        "quantity": str(quantity),
    }

    async def _post(s: aiohttp.ClientSession) -> dict:
        async with s.post(PLACE_ORDER_URL, json=payload, headers=headers) as resp:
            return await resp.json()

    if session is not None:
        return await _post(session)
    async with aiohttp.ClientSession() as own_session:
        return await _post(own_session)