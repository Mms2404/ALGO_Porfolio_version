"""Admin-only strategy trigger endpoints.

Authentication: Supabase JWT with role='AP' in profiles table.

POST /api/admin/trigger/stock-hedge/
  Body: {"symbol": "HDFCBANK", "confirmed": false}
    -> Returns trade details (expiry, put/futures info, estimated cost)
  Body: {"symbol": "HDFCBANK", "confirmed": true}
    -> Starts the Celery task, returns signal info

POST /api/admin/trigger/opening-bell/
  Body: {"instrument": "NIFTY", "confirmed": true}
    -> Starts opening bell (admin override of the scheduled task)

POST /api/admin/trigger/jackpot/
  Body: {"confirmed": true}
    -> Starts jackpot (admin override)
"""

from __future__ import annotations

import logging
from datetime import datetime

import pytz
from rest_framework.decorators import api_view
from rest_framework.response import Response

from apps.marketdata.instruments import get_stock_future
from apps.marketdata.expiry import get_safe_expiry
from apps.marketdata.feed_rest import get_stock_ltp
from apps.marketdata.nifty50 import NIFTY50_BY_SECTOR, is_valid_symbol
from apps.marketdata.strikes import find_stock_atm_strike
from core.angel.auth import login_to_angel
from core.responses import error_response, success_response
from core.supabase.client import get_client, verify_supabase_access_token

IST = pytz.timezone("Asia/Kolkata")

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _get_ap_user(request) -> str | None:
    """Verify JWT and confirm role='AP'. Returns user_id or None."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        logger.warning("_get_ap_user: missing/malformed Authorization header")
        return None
    try:
        token = auth.replace("Bearer ", "").strip()
        user_id = verify_supabase_access_token(token)
        profile = (
            get_client()
            .table("profiles")
            .select("role")
            .eq("id", user_id)
            .single()
            .execute()
        )
        role = profile.data.get("role") if profile.data else None
        if role == "AP":
            return user_id
        logger.warning("_get_ap_user: user %s has role=%r, expected 'AP'", user_id, role)
        return None
    except Exception as exc:
        logger.warning("_get_ap_user: token verification failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Stock Hedge trigger
# ---------------------------------------------------------------------------

@api_view(["GET", "POST"])
def trigger_stock_hedge(request):
    """GET  -> returns Nifty 50 stock list for the dropdown.
       POST -> confirmed=false: preview; confirmed=true: start trade.
    """
    ap_user = _get_ap_user(request)
    if not ap_user:
        return Response(error_response("Unauthorized. AP role required.", "AP_AUTH_REQUIRED"), status=403)

    # GET: return nifty50 dropdown list
    if request.method == "GET":
        stocks = [
            {"sector": sector, "name": name, "symbol": sym}
            for sector, companies in NIFTY50_BY_SECTOR.items()
            for name, sym in companies.items()
        ]
        return Response(success_response(data={"stocks": stocks}))

    # POST: preview or trigger
    symbol    = request.data.get("symbol", "").upper()
    confirmed = request.data.get("confirmed", False)
    mode      = request.data.get("mode", "DRY").upper()

    if not symbol:
        return Response(error_response("symbol is required", "MISSING_SYMBOL"), status=400)
    if not is_valid_symbol(symbol):
        return Response(error_response(f"{symbol} is not in Nifty 50 list", "INVALID_SYMBOL"), status=400)
    if mode not in ("LIVE", "DRY"):
        return Response(error_response("mode must be LIVE or DRY", "INVALID_MODE"), status=400)

    # Login feed account to get LTP
    from core.angel.feed_accounts import FEED_ACCOUNTS
    creds = FEED_ACCOUNTS[1]
    login_res = login_to_angel(
        client_code=creds["client_id"], password=creds["password"],
        totp_secret=creds["totp_secret"], api_key=creds["api_key"],
    )
    if not login_res.get("status"):
        return Response(error_response("Feed login failed", "FEED_LOGIN_FAILED"), status=500)

    td      = login_res["data"]
    jwt     = td["jwt_token"]
    api_key = creds["api_key"]

    try:
        spot   = get_stock_ltp(symbol, api_key, jwt)
        expiry = get_safe_expiry(symbol, "OPTSTK")
        if not expiry:
            return Response(error_response("No safe expiry found", "NO_EXPIRY"), status=500)

        atm = find_stock_atm_strike(symbol, spot, expiry)
        if not atm:
            return Response(error_response("ATM strike not found", "NO_ATM"), status=500)

        fut_inst = get_stock_future(symbol, expiry)
        if not fut_inst:
            return Response(error_response("No futures found", "NO_FUTURES"), status=500)

        put_inst = atm["pe_token"]
        lot_size = int(put_inst.get("lotsize", 1))

        # Get LTP for PUT and FUTURES
        from apps.marketdata.feed_rest import _ltp_quote
        fetched = _ltp_quote(api_key, jwt, {
            "NFO": [put_inst["token"], fut_inst["token"]]
        })
        ltp_map = {str(f["symbolToken"]): float(f["ltp"]) for f in fetched}
        put_ltp = ltp_map.get(put_inst["token"], 0.0)
        fut_ltp = ltp_map.get(fut_inst["token"], 0.0)

        est_put_cost     = put_ltp * lot_size
        est_futures_cost = fut_ltp * lot_size
        est_total        = est_put_cost + est_futures_cost

        preview_data = {
            "symbol":              symbol,
            "spot":                spot,
            "expiry":              expiry,
            "strike":              atm["strike"],
            "put_symbol":          put_inst["symbol"],
            "put_token":           put_inst["token"],
            "put_ltp":             put_ltp,
            "futures_symbol":      fut_inst["symbol"],
            "futures_token":       fut_inst["token"],
            "futures_ltp":         fut_ltp,
            "lot_size":            lot_size,
            "est_put_cost":        round(est_put_cost, 2),
            "est_futures_cost":    round(est_futures_cost, 2),
            "est_total_cost":      round(est_total, 2),
            "order_note":          "PUT will be bought first, FUTURES second",
            "cost_note":           "Futures cost shown is LTP × lot_size. Actual margin may vary.",
        }

    except Exception as exc:
        logger.error("stock_hedge preview failed: %s", exc)
        return Response(error_response(str(exc), "PREVIEW_ERROR"), status=500)

    if not confirmed:
        return Response(success_response(data=preview_data, message="Preview ready — send confirmed=true to start"))

    # confirmed=true: start Celery task
    from apps.scheduling.tasks import run_stock_hedge_task
    task = run_stock_hedge_task.delay(symbol=symbol, mode=mode)
    return Response(success_response(
        data={**preview_data, "task_id": task.id},
        message=f"Stock hedge started for {symbol}",
    ))


# ---------------------------------------------------------------------------
# Opening Bell manual trigger
# ---------------------------------------------------------------------------

@api_view(["POST"])
def trigger_opening_bell(request):
    ap_user = _get_ap_user(request)
    if not ap_user:
        return Response(error_response("Unauthorized. AP role required.", "AP_AUTH_REQUIRED"), status=403)

    instrument = request.data.get("instrument", "NIFTY").upper()
    confirmed  = request.data.get("confirmed", False)

    if not confirmed:
        return Response(success_response(
            data={"instrument": instrument},
            message="Send confirmed=true to start opening bell",
        ))

    from apps.scheduling.tasks import run_opening_bell_task
    task = run_opening_bell_task.delay(instrument=instrument)
    return Response(success_response(
        data={"task_id": task.id, "instrument": instrument},
        message=f"Opening bell started for {instrument}",
    ))


# ---------------------------------------------------------------------------
# Jackpot manual trigger
# ---------------------------------------------------------------------------

@api_view(["POST"])
def trigger_jackpot(request):
    ap_user = _get_ap_user(request)
    if not ap_user:
        return Response(error_response("Unauthorized. AP role required.", "AP_AUTH_REQUIRED"), status=403)

    confirmed = request.data.get("confirmed", False)
    if not confirmed:
        return Response(success_response(message="Send confirmed=true to start jackpot"))

    from apps.scheduling.tasks import run_jackpot_task
    task = run_jackpot_task.delay()
    return Response(success_response(
        data={"task_id": task.id},
        message="Jackpot started",
    ))


# ---------------------------------------------------------------------------
# Test strategy — manual single-leg order (strategy_id=8)
# ---------------------------------------------------------------------------

@api_view(["POST"])
def trigger_test(request):
    """POST /api/admin/trigger/test/
    Body: {"instrument": "NIFTY"|"SENSEX", "strike": 24800, "option_type": "CE"|"PE",
           "side": "BUY"|"SELL", "confirmed": true,
           "trigger_time": "14:30"}   <- optional, "HH:MM" 24-hour IST, today
      -> Without trigger_time: fires immediately.
      -> With trigger_time: schedules the order for that clock time today
         (Celery eta) instead of running right away. If that time has
         already passed today, this is rejected rather than firing
         immediately, so you don't get a silent surprise.
      Requires a {id: 8, name: "Test", is_enabled: true} row in the
      strategies table.
    """
    ap_user = _get_ap_user(request)
    if not ap_user:
        return Response(error_response("Unauthorized. AP role required.", "AP_AUTH_REQUIRED"), status=403)

    instrument   = request.data.get("instrument", "").upper()
    strike       = request.data.get("strike")
    option_type  = request.data.get("option_type", "").upper()
    side         = request.data.get("side", "").upper()
    confirmed    = request.data.get("confirmed", False)
    trigger_time = request.data.get("trigger_time")  # optional "HH:MM"
    mode         = request.data.get("mode", "LIVE").upper()  # LIVE or DRY

    if instrument not in ("NIFTY", "SENSEX"):
        return Response(error_response("instrument must be NIFTY or SENSEX", "INVALID_INSTRUMENT"), status=400)
    if strike in (None, ""):
        return Response(error_response("strike is required", "MISSING_STRIKE"), status=400)
    if option_type not in ("CE", "PE"):
        return Response(error_response("option_type must be CE or PE", "INVALID_OPTION_TYPE"), status=400)
    if side not in ("BUY", "SELL"):
        return Response(error_response("side must be BUY or SELL", "INVALID_SIDE"), status=400)
    if mode not in ("LIVE", "DRY"):
        return Response(error_response("mode must be LIVE or DRY", "INVALID_MODE"), status=400)

    try:
        strike = float(strike)
    except (TypeError, ValueError):
        return Response(error_response("strike must be a number", "INVALID_STRIKE"), status=400)

    scheduled_dt = None
    if trigger_time:
        try:
            hh, mm = trigger_time.strip().split(":")
            now_ist = datetime.now(IST)
            scheduled_dt = IST.localize(datetime(now_ist.year, now_ist.month, now_ist.day, int(hh), int(mm)))
        except (ValueError, AttributeError):
            return Response(error_response(
                "trigger_time must be 'HH:MM' 24-hour IST, e.g. '14:30'", "INVALID_TRIGGER_TIME"), status=400)

        if scheduled_dt <= datetime.now(IST):
            return Response(error_response(
                f"trigger_time {trigger_time} has already passed today", "TRIGGER_TIME_IN_PAST"), status=400)

    if not confirmed:
        data = {"instrument": instrument, "strike": strike, "option_type": option_type, "side": side}
        if scheduled_dt:
            data["trigger_time"] = trigger_time
        return Response(success_response(
            data=data,
            message="Send confirmed=true to place this test order"
                    + (f" (scheduled for {trigger_time} IST)" if scheduled_dt else ""),
        ))

    from apps.scheduling.tasks import run_test_task
    task_kwargs = dict(instrument=instrument, strike=strike, option_type=option_type, side=side, mode=mode)

    if scheduled_dt:
        task = run_test_task.apply_async(kwargs=task_kwargs, eta=scheduled_dt)
        message = f"Test {side} scheduled for {trigger_time} IST — {instrument} {strike} {option_type}"
    else:
        task = run_test_task.delay(**task_kwargs)
        message = f"Test {side} started for {instrument} {strike} {option_type}"

    return Response(success_response(
        data={"task_id": task.id, "instrument": instrument, "strike": strike,
              "option_type": option_type, "side": side,
              "trigger_time": trigger_time, "scheduled": scheduled_dt is not None},
        message=message,
    ))


# ---------------------------------------------------------------------------
# Active overnight trades
# ---------------------------------------------------------------------------

@api_view(["GET"])
def active_overnight_trades(request):
    ap_user = _get_ap_user(request)
    if not ap_user:
        return Response(error_response("Unauthorized. AP role required.", "AP_AUTH_REQUIRED"), status=403)

    trades = get_client().table("overnight_trades")\
        .select("*").eq("status", "active").order("opened_at", desc=True).execute().data or []
    return Response(success_response(data={"trades": trades}))
