"""Celery tasks for scheduling and token refresh.

Tasks
-----
refresh_all_tokens   : refresh Angel jwt/feed for every verified account (09:00 IST)
run_jackpot_task     : fire jackpot strategy (~14:40 IST)
run_opening_bell_task: fire opening bell strategy (09:15 IST)

All strategy tasks run asyncio.run() — safe because each Celery worker
process gets a fresh event loop per task.
"""

from __future__ import annotations

import asyncio
import logging

from celery import shared_task

from core.conditions import is_trading_day

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token refresh ( not using refresh token , just re-login to get new tokens - to avoid complications of refresh token expiry and rotation )
# ---------------------------------------------------------------------------

@shared_task(name="apps.scheduling.tasks.refresh_all_tokens", bind=True, max_retries=2)
def refresh_all_tokens(self):
    if not is_trading_day():
        logger.info("refresh_all_tokens: not a trading day, skipped")
        return {"skipped": True, "reason": "not a trading day"}

    from core.angel.auth import login_to_angel
    from core.crypto.cipher import decrypt_with_private_key, encrypt_token
    from core.supabase.client import get_client
    from datetime import datetime
    import pytz

    sb = get_client()
    rows = (
        sb.table("angel_accounts")
        .select("id, client_id, api_key, pin, totp_secret, is_verified")
        .eq("is_verified", True)
        .execute()
    ).data or []

    logger.info("refresh_all_tokens: %d verified accounts", len(rows))
    success, failed = 0, 0

    for row in rows:
        client_id = row["client_id"]
        try:
            def _dec(v):
                if isinstance(v, memoryview): v = v.tobytes()
                if isinstance(v, bytes): v = v.decode("utf-8")
                return v

            api_key     = decrypt_with_private_key(_dec(row["api_key"]))
            pin         = decrypt_with_private_key(_dec(row["pin"]))
            totp_secret = decrypt_with_private_key(_dec(row["totp_secret"]))

            login_res = login_to_angel(
                client_code=client_id,
                password=pin,
                totp_secret=totp_secret,
                api_key=api_key,
            )
            if not login_res.get("status"):
                raise Exception(login_res.get("message"))

            tokens = login_res["data"]
            sb.table("angel_accounts").update({
                "jwt_token":     encrypt_token(tokens["jwt_token"]),
                "feed_token":    encrypt_token(tokens["feed_token"]),
                "refresh_token": encrypt_token(tokens["refresh_token"]),
                "updated_at":    datetime.now(pytz.utc).isoformat(),
            }).eq("id", row["id"]).execute()

            logger.info("[%s] tokens refreshed OK", client_id)
            success += 1

        except Exception as exc:
            logger.error("[%s] token refresh failed: %s", client_id, exc)
            failed += 1

    result = {"success": success, "failed": failed}
    logger.info("refresh_all_tokens done: %s", result)
    return result


# ---------------------------------------------------------------------------
# Strategy tasks
# ---------------------------------------------------------------------------

@shared_task(name="apps.scheduling.tasks.run_jackpot_task")
def run_jackpot_task(instrument: str | None = None, mode: str = "DRY"):
    """Trigger jackpot strategy. Scheduled ~14:40 IST by Celery Beat.

    `instrument`: explicit override always wins (manual trigger). If not
    given, reads today's shared NIFTY/SENSEX pick (see
    apps.scheduling.instrument_selector), same one Strangle/Opening Bell use.
    `mode`: "LIVE" or "DRY" -- set per-strategy directly in celery.py's
    Beat schedule kwargs. No longer read from a global TRADE_MODE env var.
    """
    if not is_trading_day():
        logger.info("run_jackpot_task: not a trading day, skipped")
        return {"skipped": True}

    if instrument is None:
        from apps.scheduling.instrument_selector import get_todays_instrument
        instrument = get_todays_instrument()

    from apps.strategies.jackpot import run_jackpot
    logger.info("run_jackpot_task: instrument=%s mode=%s", instrument, mode)
    try:
        asyncio.run(run_jackpot(instrument=instrument, mode=mode))
        logger.info("run_jackpot_task: completed")
        return {"status": "completed"}
    except Exception as exc:
        logger.error("run_jackpot_task failed: %s", exc)
        return {"status": "error", "error": str(exc)}


@shared_task(name="apps.scheduling.tasks.run_opening_bell_task")
def run_opening_bell_task(instrument: str | None = None, mode: str = "DRY"):
    """Trigger opening bell strategy. Scheduled 09:22 IST by Celery Beat.

    `instrument`: explicit override always wins (manual trigger). If not
    given, reads today's shared NIFTY/SENSEX pick (see
    apps.scheduling.instrument_selector), same one Strangle/Jackpot use.
    `mode`: "LIVE" or "DRY" -- set per-strategy directly in celery.py's
    Beat schedule kwargs. No longer read from a global TRADE_MODE env var.
    """
    if not is_trading_day():
        logger.info("run_opening_bell_task: not a trading day, skipped")
        return {"skipped": True}

    if instrument is None:
        from apps.scheduling.instrument_selector import get_todays_instrument
        instrument = get_todays_instrument()

    from apps.strategies.opening_bell import run_opening_bell
    logger.info("run_opening_bell_task: instrument=%s mode=%s", instrument, mode)
    try:
        asyncio.run(run_opening_bell(instrument=instrument, mode=mode))
        logger.info("run_opening_bell_task: completed")
        return {"status": "completed"}
    except Exception as exc:
        logger.error("run_opening_bell_task failed: %s", exc)
        return {"status": "error", "error": str(exc)}


@shared_task(name="apps.scheduling.tasks.run_stock_hedge_task")
def run_stock_hedge_task(symbol: str, mode: str = "DRY"):
    """Start a new stock hedge trade for the given symbol."""
    if not is_trading_day():
        logger.info("run_stock_hedge_task: not a trading day, skipped")
        return {"skipped": True}

    from apps.strategies.stock_hedge import run_stock_hedge
    logger.info("run_stock_hedge_task: symbol=%s mode=%s", symbol, mode)
    try:
        asyncio.run(run_stock_hedge(symbol=symbol, mode=mode))
        return {"status": "completed"}
    except Exception as exc:
        logger.error("run_stock_hedge_task failed: %s", exc)
        return {"status": "error", "error": str(exc)}



@shared_task(name="apps.scheduling.tasks.run_strangle_task")
def run_strangle_task(instrument: str | None = None, mode: str = "DRY"):
    """
    Trigger Strangle strategy (strategy_id=6).
    Buys OTM CE (above spot) + OTM PE (below spot) — cheaper than straddle.
    Exit: +70% profit | -25% SL | 15:25 force exit

    Manual trigger (explicit instrument always wins):
        run_strangle_task.delay(instrument="BANKNIFTY")

    Scheduled via Beat with NO instrument kwarg (see celery.py) — in that
    case this reads today's shared NIFTY/SENSEX pick (whichever has the
    nearer expiry today), via apps.scheduling.instrument_selector -- same
    one Opening Bell/Jackpot use.

    `mode`: "LIVE" or "DRY" -- set per-strategy directly in celery.py's
    Beat schedule kwargs. No longer read from a global TRADE_MODE env var.
    """
    if not is_trading_day():
        return {"skipped": True}
    if instrument is None:
        from apps.scheduling.instrument_selector import get_todays_instrument
        instrument = get_todays_instrument()
    from apps.strategies.strangle import run_strangle
    logger.info("run_strangle_task: instrument=%s mode=%s", instrument, mode)
    try:
        asyncio.run(run_strangle(instrument=instrument, mode=mode))
        return {"status": "completed"}
    except Exception as exc:
        logger.error("run_strangle_task failed: %s", exc)
        return {"status": "error", "error": str(exc)}

    

#---------------------------------------------------------------------------
# FUTURE strategies — tasks are COMMENTED until ready to trade live
# ---------------------------------------------------------------------------

# @shared_task(name="apps.scheduling.tasks.run_long_straddle_task")
# def run_long_straddle_task(
#     index_name: str = "NIFTY",
#     expiry: str | None = None,
#     force_exit_hour: int = 13,
#     force_exit_minute: int = 50,
# ):
#     """
#     Trigger Long Straddle (strategy_id=6).
#     Buy ATM CE + ATM PE on the given index; exit at +30% / -10% / time.
#
#     Args:
#         index_name       : "NIFTY" | "BANKNIFTY" | "FINNIFTY"
#         expiry           : "27JUN2025" — None auto-picks nearest weekly
#         force_exit_hour  : IST hour for time exit (default 13)
#         force_exit_minute: IST minute for time exit (default 50)
#
#     Celery Beat example (celery.py):
#         {
#             "task": "apps.scheduling.tasks.run_long_straddle_task",
#             "schedule": crontab(hour=9, minute=15),
#             "kwargs": {"index_name": "NIFTY"},
#         }
#
#     Manual trigger:
#         run_long_straddle_task.delay(index_name="BANKNIFTY", expiry="27JUN2025")
#
#     To enable: uncomment this block + import below.
#     """
#     if not is_trading_day():
#         return {"skipped": True}
#
#     from apps.strategies.long_straddle import run_long_straddle
#     logger.info("run_long_straddle_task: index=%s expiry=%s", index_name, expiry)
#     try:
#         asyncio.run(run_long_straddle(
#             index_name=index_name,
#             expiry=expiry,
#             force_exit_hour=force_exit_hour,
#             force_exit_minute=force_exit_minute,
#         ))
#         return {"status": "completed"}
#     except Exception as exc:
#         logger.error("run_long_straddle_task failed: %s", exc)
#         return {"status": "error", "error": str(exc)}


# @shared_task(name="apps.scheduling.tasks.run_stock_future_hedge_task")
# def run_stock_future_hedge_task(
#     stock_name: str,
#     expiry: str | None = None,
#     exit_date: str | None = None,
#     exit_time: str = "14:30",
# ):
#     """
#     Trigger 4-leg Stock Future Hedge (strategy_id=7).
#     Buy FUTURE + ATM PE + OTM CE + OTM PE; exit at +30% / -10% / datetime.
#
#     NOTE: executor.py _build_legs needs extending before live orders work.
#           See TODO comment in stock_future_hedge.py.
#
#     Args:
#         stock_name : e.g. "TCS", "HDFCBANK" (must exist in STOCK_STRIKE_STEPS)
#         expiry     : "27JUN2025" — None auto-picks via get_safe_expiry()
#         exit_date  : "YYYY-MM-DD" — None defaults to expiry day
#         exit_time  : "HH:MM" IST (default "14:30")
#
#     Manual trigger:
#         run_stock_future_hedge_task.delay(
#             stock_name="TCS", exit_date="2025-06-27", exit_time="14:00"
#         )
#
#     To enable: uncomment this block + import below.
#     """
#     if not is_trading_day():
#         return {"skipped": True}
#
#     import pytz
#     from datetime import datetime
#     from apps.strategies.stock_future_hedge import run_stock_future_hedge
#
#     force_exit_dt = None
#     if exit_date:
#         try:
#             IST = pytz.timezone("Asia/Kolkata")
#             d   = exit_date.split("-")
#             t   = exit_time.split(":")
#             force_exit_dt = IST.localize(
#                 datetime(int(d[0]), int(d[1]), int(d[2]), int(t[0]), int(t[1]))
#             )
#         except Exception as e:
#             logger.error("Invalid exit_date/exit_time: %s", e)
#             return {"status": "error", "error": str(e)}
#
#     logger.info("run_stock_future_hedge_task: stock=%s expiry=%s exit=%s", stock_name, expiry, force_exit_dt)
#     try:
#         asyncio.run(run_stock_future_hedge(
#             stock_name=stock_name,
#             expiry=expiry,
#             force_exit_datetime=force_exit_dt,
#         ))
#         return {"status": "completed"}
#     except Exception as exc:
#         logger.error("run_stock_future_hedge_task failed: %s", exc)
#         return {"status": "error", "error": str(exc)}


@shared_task(name="apps.scheduling.tasks.resume_overnight_trades_task")
def resume_overnight_trades_task():
    """Resume all active overnight trades after server restart. Runs at 09:14 IST."""
    if not is_trading_day():
        logger.info("resume_overnight_trades_task: not a trading day, skipped")
        return {"skipped": True}

    from apps.strategies.stock_hedge import get_active_trades, resume_stock_hedge
    from core.supabase.client import get_client

    trades = get_active_trades()
    if not trades:
        logger.info("resume_overnight_trades_task: no active trades")
        return {"resumed": 0}

    logger.info("resume_overnight_trades_task: resuming %d trades", len(trades))
    for trade in trades:
        # Resume with the SAME mode the original entry used -- look it up
        # from the linked signal rather than defaulting, so a resumed
        # position can't silently switch from LIVE to DRY (or vice versa).
        mode = "DRY"
        try:
            sig_resp = (
                get_client().table("signals").select("payload")
                .eq("id", trade["signal_id"]).single().execute()
            )
            mode = (sig_resp.data or {}).get("payload", {}).get("mode", "DRY").upper()
        except Exception as exc:
            logger.warning(
                "resume_overnight_trades_task: couldn't look up mode for trade %s, defaulting DRY: %s",
                trade["id"], exc,
            )
        asyncio.run(resume_stock_hedge(
            trade_id=trade["id"],
            symbol=trade["symbol"],
            mode=mode,
        ))
    return {"resumed": len(trades)}


@shared_task(name="apps.scheduling.tasks.snapshot_overnight_pnl_task")
def snapshot_overnight_pnl_task():
    """
    Fetch current LTP for all active overnight trades and persist P&L to DB.
    Runs at 15:30 IST — gives you an EOD snapshot before market close.
    Also runs at 11:00 and 13:00 for intraday visibility.
    """
    if not is_trading_day():
        return {"skipped": True}

    from apps.strategies.stock_hedge import get_active_trades, _update_pnl_snapshot
    from core.angel.auth import login_to_angel
    from core.angel.feed_accounts import FEED_ACCOUNTS

    trades = get_active_trades()
    if not trades:
        logger.info("snapshot_overnight_pnl_task: no active trades")
        return {"snapshots": 0}

    # Login once for all LTP fetches
    creds = FEED_ACCOUNTS[0]
    login_res = login_to_angel(
        client_code=creds["client_id"], password=creds["password"],
        totp_secret=creds["totp_secret"], api_key=creds["api_key"],
    )
    if not login_res.get("status"):
        logger.error("snapshot_overnight_pnl_task: login failed: %s", login_res.get("message"))
        return {"error": "login_failed"}

    jwt     = login_res["data"]["jwt_token"]
    api_key = creds["api_key"]
    updated = 0

    for trade in trades:
        try:
            symbol   = trade["symbol"]
            lot_size = int(trade["lot_size"])
            invested = float(trade.get("invested_amount") or 0)

            from apps.marketdata.feed_rest import _ltp_quote

            # Fetch PUT and FUTURES LTP together in one API call (both NFO)
            nfo_result = _ltp_quote(api_key, jwt, {
                "NFO": [trade["put_token"], trade["futures_token"]]
            })
            ltp_map = {str(r["symbolToken"]): float(r["ltp"]) for r in nfo_result}

            cur_put = ltp_map.get(str(trade["put_token"]), 0.0)
            cur_fut = ltp_map.get(str(trade["futures_token"]), 0.0)

            if cur_put == 0 or cur_fut == 0:
                logger.warning("snapshot [%s]: got zero LTP — put=%s fut=%s", trade["symbol"], cur_put, cur_fut)
                continue

            entry_put = float(trade["put_entry_price"])
            entry_fut = float(trade["futures_entry_price"])

            pnl_amount = ((cur_put - entry_put) + (cur_fut - entry_fut)) * lot_size
            pnl_pct    = pnl_amount / invested if invested else 0

            _update_pnl_snapshot(
                trade["id"], cur_put, cur_fut, pnl_amount, pnl_pct,
            )
            logger.info(
                "snapshot [%s]: PUT=%.2f FUT=%.2f P&L=%.2f (%.2f%%)",
                symbol, cur_put, cur_fut, pnl_amount, round(pnl_pct * 100, 2),
            )
            updated += 1
        except Exception as exc:
            logger.error("snapshot failed for trade %s: %s", trade.get("id"), exc)

    return {"snapshots": updated}

@shared_task(name="apps.scheduling.tasks.run_test_task")
def run_test_task(instrument: str, strike: float, option_type: str, side: str, mode: str = "LIVE"):
    """Manual single-leg test order (Test strategy, id=8). Admin-triggered
    only, via admin_api; not on any Celery Beat schedule.

    `mode` defaults to LIVE here (unlike the real strategies, which default
    to DRY) since Test exists specifically to verify the real order-placement
    path -- pass mode="DRY" explicitly if you want a dry run instead.
    """
    if not is_trading_day():
        logger.info("run_test_task: not a trading day, skipped")
        return {"skipped": True}

    from apps.strategies.test_strategy import run_test_order
    logger.info(
        "run_test_task: instrument=%s strike=%s option_type=%s side=%s mode=%s",
        instrument, strike, option_type, side, mode,
    )
    try:
        result = asyncio.run(run_test_order(instrument, strike, option_type, side, mode))
        logger.info("run_test_task: completed: %s", result)
        return result
    except Exception as exc:
        logger.error("run_test_task failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}

@shared_task(name="apps.scheduling.tasks.warm_scrip_master_task")
def warm_scrip_master_task(worker_count: int = 5):
    """Pre-download today's Angel scrip master before market open, and
    try to get it into EVERY worker process's own in-memory cache --
    not just whichever one process a single task would land on.

    Celery's prefork pool doesn't give a clean "run this on every child
    process" primitive for task dispatch (that's what worker_process_init
    is for, but you specifically wanted a scheduled-task approach instead
    of a boot hook). This is the practical alternative: fire `worker_count`
    copies in quick succession. When all N worker processes are idle
    (which they should be at this scheduled time), Celery's normal
    round-robin task distribution reliably spreads them one-per-process
    in practice -- but it's NOT a strict guarantee the way a true
    broadcast would be. Worst case, a process that didn't get one just
    falls through to the (now gzip-compressed, sub-second) Supabase
    Storage fetch on its first real use that day -- not a failure, just
    a smaller version of the original problem.

    `worker_count` should match your --concurrency value.
    """
    if not is_trading_day():
        logger.info("warm_scrip_master_task: not a trading day, skipped")
        return {"skipped": True}

    for _ in range(worker_count):
        _preload_scrip_master_once.delay()
    logger.info("warm_scrip_master_task: dispatched %d preload copies", worker_count)
    return {"dispatched": worker_count}


@shared_task(name="apps.scheduling.tasks._preload_scrip_master_once")
def _preload_scrip_master_once():
    """One unit of scrip-master preload -- see warm_scrip_master_task."""
    from apps.marketdata.instruments import fetch_scrip_master
    try:
        data = fetch_scrip_master()
        logger.info("_preload_scrip_master_once: cached %d instruments", len(data))
        return {"status": "completed", "count": len(data)}
    except Exception as exc:
        logger.error("_preload_scrip_master_once failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}


@shared_task(name="apps.scheduling.tasks.select_daily_instrument_task")
def select_daily_instrument_task():
    """Each trading morning, compare NIFTY's and SENSEX's nearest expiry
    and cache whichever is sooner -- Strangle, Opening Bell, and Jackpot
    all read this same pick later (file cache). Scheduled right after the
    scrip master warm-up, before any of those three fire."""
    logger.info("select_daily_instrument_task: task started")
    if not is_trading_day():
        logger.info("select_daily_instrument_task: not a trading day, skipped")
        return {"skipped": True}

    logger.info("select_daily_instrument_task: importing instrument_selector")
    from apps.scheduling.instrument_selector import (
        select_nearest_expiry_instrument, _write_cached_instrument,
    )
    logger.info("select_daily_instrument_task: import done, resolving instrument")
    try:
        instrument = select_nearest_expiry_instrument()
        logger.info("select_daily_instrument_task: resolved instrument=%s, writing cache", instrument)
        _write_cached_instrument(instrument)
        logger.info("select_daily_instrument_task: chose %s for today", instrument)
        return {"status": "completed", "instrument": instrument}
    except Exception as exc:
        logger.error("select_daily_instrument_task failed: %s", exc, exc_info=True)
        return {"status": "error", "error": str(exc)}
