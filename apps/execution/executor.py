"""Signal executor: fan a signal out to every subscribed user.

Mode (DRY/LIVE) is now decided PER STRATEGY, not by a global env var.
Each strategy's Celery Beat entry in celery.py carries its own "mode"
kwarg, which flows through run_xxx() -> the strategy's signal payload
-> here, read per-signal instead of a module-level constant.

DRY  : record + notify only.
LIVE : record + place actual Angel orders.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime

import pytz

from apps.execution.signals import record_execution, update_execution
from core.logging import get_logger
from core.notifications import notify_user
from core.supabase.client import get_client

logger = logging.getLogger(__name__)

IST = pytz.timezone("Asia/Kolkata")

# Dedicated log file for order-placement timing -- user, strategy, when the
# API call was sent, when it completed, and the exact duration in ms.
# logs/order_timing/order_timing_orders.log
order_timing_logger = get_logger(
    "order_timing", "orders",
    fmt="%(asctime)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _get_subscribed_users(strategy_id: int) -> list[str]:
    resp = (
        get_client()
        .table("user_strategies")
        .select("user_id")
        .eq("strategy_id", strategy_id)
        .execute()
    )
    return [row["user_id"] for row in (resp.data or [])]


def _get_angel_account(user_id: str) -> dict | None:
    resp = (
        get_client()
        .table("angel_accounts")
        .select("client_id, api_key, jwt_token, feed_token, is_verified")
        .eq("user_id", user_id)
        .single()
        .execute()
    )
    row = resp.data
    if not row or not row.get("is_verified"):
        return None
    return row


def _find_matching_entry_signal(exit_signal: dict) -> dict | None:
    """Given an EXIT signal, find the ENTRY signal it corresponds to
    (same strategy, trading day, instrument). Used to verify which users
    actually got a position filled before letting them exit it."""
    resp = (
        get_client()
        .table("signals")
        .select("id")
        .eq("strategy_id", exit_signal["strategy_id"])
        .eq("trading_day", exit_signal["trading_day"])
        .eq("instrument", exit_signal["instrument"])
        .eq("kind", "ENTRY")
        .execute()
    )
    rows = resp.data or []
    return rows[0] if rows else None


def _get_user_placed_legs(entry_signal_id: str, user_id: str) -> set[str]:
    """Which legs (e.g. {'CE', 'PE'}) this user actually got successfully
    executed for the given ENTRY signal -- 'placed' (LIVE) or 'dry'
    (DRY) both count as success; only 'failed' legs are excluded."""
    resp = (
        get_client()
        .table("signal_executions")
        .select("leg, status")
        .eq("signal_id", entry_signal_id)
        .eq("user_id", user_id)
        .in_("status", ["placed", "dry"])
        .execute()
    )
    return {row["leg"] for row in (resp.data or [])}


def _build_legs(signal: dict) -> list[dict]:
    payload = signal.get("payload") or {}
    kind    = signal.get("kind", "ENTRY")
    side    = "BUY" if kind == "ENTRY" else "SELL"
    lots    = int(payload.get("lots", 1))
    lot_size = int(payload.get("lot_size", 1))
    qty     = lots * lot_size

    # Prices: entry_* for ENTRY ticks, exit_* for EXIT ticks
    if kind == "ENTRY":
        ce_price  = payload.get("entry_ce")
        pe_price  = payload.get("entry_pe")
        fut_price = payload.get("entry_fut")
    else:
        ce_price  = payload.get("exit_ce")
        pe_price  = payload.get("exit_pe")
        fut_price = payload.get("exit_fut")

    ce_leg = pe_leg = fut_leg = None

    if payload.get("ce_symbol"):
        ce_leg = {
            "leg":         "CE",
            "side":        side,
            "symbol":      payload["ce_symbol"],
            "token":       payload.get("ce_token"),
            "qty":         qty,
            "price":       ce_price,
            "entry_price": payload.get("entry_ce"),
        }
    if payload.get("pe_symbol"):
        pe_leg = {
            "leg":         "PE",
            "side":        side,
            "symbol":      payload["pe_symbol"],
            "token":       payload.get("pe_token"),
            "qty":         qty,
            "price":       pe_price,
            "entry_price": payload.get("entry_pe"),
        }
    if payload.get("fut_symbol"):
        fut_leg = {
            "leg":         "FUT",
            "side":        side,
            "symbol":      payload["fut_symbol"],
            "token":       payload.get("fut_token"),
            "qty":         qty,
            "price":       fut_price,
            "entry_price": payload.get("entry_fut"),
        }

    legs = []
    if ce_leg:
        legs.append(ce_leg)

    if pe_leg and fut_leg:
        # stock_hedge: compulsory order — PUT before FUTURES on entry,
        # FUTURES before PUT on exit.
        if kind == "ENTRY":
            legs.append(pe_leg)
            legs.append(fut_leg)
        else:
            legs.append(fut_leg)
            legs.append(pe_leg)
    else:
        if pe_leg:
            legs.append(pe_leg)
        if fut_leg:
            legs.append(fut_leg)

    return legs


def _notification_message(signal: dict, leg: dict) -> str:
    payload       = signal.get("payload") or {}
    kind          = signal.get("kind", "ENTRY")
    strategy_name = payload.get("strategy_name", "Strategy")
    index         = payload.get("index", "")
    opt_type      = leg["leg"]
    qty           = leg["qty"]
    price         = leg.get("price") or 0.0 
    exit_reason   = payload.get("exit_reason", "")

    if opt_type == "FUT":
        # Futures have no strike — don't show the PUT's strike next to them.
        leg_label = f"{index} FUT"
    else:
        strike = payload.get("pe_strike") if opt_type == "PE" and payload.get("pe_strike") else payload.get("strike", "")
        leg_label = f"{index} {strike} {opt_type}"

    if kind == "ENTRY":
        total_used = qty * price
        return (
            f"[{strategy_name}] BUY {leg_label} | "
            f"Qty: {qty} | Price: Rs.{price:.2f} | Total Used: Rs.{total_used:.2f}"
        )
    else:
        entry_price = leg.get("entry_price") or 0.0
        pnl         = (price - entry_price) * qty
        total_got   = price * qty
        pnl_sign    = "+" if pnl >= 0 else ""
        reason_str  = f" [{exit_reason}]" if exit_reason else ""
        return (
            f"[{strategy_name}] SELL {leg_label}{reason_str} | "
            f"Qty: {qty} | Entry: Rs.{entry_price:.2f} | Exit: Rs.{price:.2f} | "
            f"P&L: {pnl_sign}Rs.{pnl:.2f} | Total Got: Rs.{total_got:.2f}"
        )


async def fan_out_signal(signal: dict) -> None:
    strategy_id = signal["strategy_id"]
    signal_id   = signal["id"]
    kind        = signal.get("kind", "ENTRY")
    mode = (signal.get("payload") or {}).get("mode", "DRY").upper()
    if mode not in ("DRY", "LIVE"):
        logger.warning("signal %s has invalid mode=%r, defaulting to DRY", signal_id, mode)
        mode = "DRY"

    user_ids = await asyncio.to_thread(_get_subscribed_users, strategy_id)
    if not user_ids:
        logger.info("No users subscribed to strategy %s", strategy_id)
        return

    legs = _build_legs(signal)
    if not legs:
        logger.warning("signal %s has no legs", signal_id)
        return

    if kind == "EXIT":
        await _fan_out_exit(signal, user_ids, legs, mode)
        return

    logger.info(
        "Fanning out signal %s (%s) to %d users, %d legs, mode=%s",
        signal_id, kind, len(user_ids), len(legs), mode,
    )
    await asyncio.gather(
        *[_process_user(signal, uid, legs, mode) for uid in user_ids],
        return_exceptions=True,
    )


async def _fan_out_exit(signal: dict, user_ids: list[str], legs: list[dict], mode: str) -> None:
    """EXIT-specific fan-out: only send each user the legs they actually
    hold, per their ENTRY execution rows -- never place a SELL for a leg
    a user never actually got a BUY filled on (e.g. their entry was
    skipped for insufficient balance, or one leg of a multi-leg entry
    was rejected while the other succeeded -- the exact naked-position
    scenario this fixes).

    If no matching ENTRY signal can be found at all (e.g. the Test
    strategy firing a standalone SELL with no prior entry, by design),
    falls back to the old unfiltered behavior rather than blocking --
    only strategies that DO have a real preceding entry get the
    stricter, position-verified check.
    """
    signal_id = signal["id"]
    entry_signal = await asyncio.to_thread(_find_matching_entry_signal, signal)

    if entry_signal is None:
        logger.info(
            "EXIT signal %s: no matching ENTRY signal found -- fanning out "
            "unfiltered (expected for standalone test/manual signals)",
            signal_id,
        )
        logger.info(
            "Fanning out signal %s (EXIT) to %d users, %d legs, mode=%s",
            signal_id, len(user_ids), len(legs), mode,
        )
        await asyncio.gather(
            *[_process_user(signal, uid, legs, mode) for uid in user_ids],
            return_exceptions=True,
        )
        return

    tasks = []
    included = 0
    for uid in user_ids:
        placed_legs = await asyncio.to_thread(_get_user_placed_legs, entry_signal["id"], uid)
        if not placed_legs:
            logger.info(
                "EXIT signal %s: user=%s has no placed entry legs — skipping entirely",
                signal_id, uid[:8],
            )
            continue

        user_legs = [leg for leg in legs if leg["leg"] in placed_legs]
        missing = [leg["leg"] for leg in legs if leg["leg"] not in placed_legs]
        if missing:
            logger.warning(
                "EXIT signal %s: user=%s only holds %s (missing %s from entry) — "
                "exiting only the legs they actually hold",
                signal_id, uid[:8], sorted(placed_legs), missing,
            )
        tasks.append(_process_user(signal, uid, user_legs, mode))
        included += 1

    logger.info(
        "Fanning out signal %s (EXIT) to %d/%d subscribed users (post-position-check), mode=%s",
        signal_id, included, len(user_ids), mode,
    )
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


async def _process_user(signal: dict, user_id: str, legs: list[dict], mode: str) -> None:
    account = await asyncio.to_thread(_get_angel_account, user_id)
    if account is None:
        return

    api_key = jwt = None

    # Balance check only applies in LIVE mode -- DRY places no real orders,
    # so a real Angel balance fetch has nothing to gate. Decrypting once
    # here also means _live_run no longer re-decrypts per leg.
    if mode == "LIVE":
        from core.angel.balance import get_balance_direct
        from core.conditions import can_user_execute
        from core.crypto.cipher import decrypt_token, decrypt_with_private_key

        try:
            api_key = decrypt_with_private_key(account["api_key"])
            jwt     = decrypt_token(account["jwt_token"])
        except Exception as exc:
            logger.error("[LIVE] user=%s credential decrypt failed, skipping: %s", user_id[:8], exc)
            await asyncio.to_thread(
                notify_user, user_id, "ERROR",
                "Your trade was skipped — we couldn't access your account credentials. Please re-verify your Angel account.",
            )
            return

        required_margin = sum(leg["qty"] * (leg.get("price") or 0) for leg in legs)
        try:
            balance = await asyncio.to_thread(get_balance_direct, api_key, jwt)
        except Exception as exc:
            logger.error("[LIVE] user=%s balance fetch failed, skipping: %s", user_id[:8], exc)
            await asyncio.to_thread(
                notify_user, user_id, "ERROR",
                "Your trade was skipped — we couldn't verify your account balance. Please check your Angel account.",
            )
            return

        gate = can_user_execute(
            user_id, signal.get("strategy_name", ""), balance, required_margin=required_margin,
        )
        if not gate.allowed:
            logger.info("[LIVE] user=%s trade skipped: %s", user_id[:8], gate.reason)
            await asyncio.to_thread(
                notify_user, user_id, "ERROR",
                f"Your trade was skipped — insufficient balance (need Rs.{required_margin:.2f}, "
                f"available Rs.{balance:.2f}).",
            )
            return

    for leg in legs:
        exec_row = await asyncio.to_thread(
            record_execution,
            signal["id"], user_id, leg["leg"], leg["side"],
            symbol=leg["symbol"],
            token=leg["token"],
            qty=leg["qty"],
            price=leg.get("price"),
            status="pending",
        )
        if exec_row is None:
            continue

        if mode == "DRY":
            await _dry_run(signal, user_id, leg, exec_row["id"])
        else:
            await _live_run(signal, user_id, leg, exec_row["id"], api_key, jwt)


async def _dry_run(signal: dict, user_id: str, leg: dict, exec_id: str) -> None:
    try:
        msg = _notification_message(signal, leg)
        await asyncio.to_thread(notify_user, user_id, leg["side"], msg)
        await asyncio.to_thread(update_execution, exec_id, status="dry")
        logger.info("[DRY] user=%s leg=%s: %s", user_id[:8], leg["leg"], msg)
    except Exception as exc:
        logger.error("[DRY] notify failed user=%s leg=%s: %s", user_id[:8], leg["leg"], exc)
        await asyncio.to_thread(update_execution, exec_id, status="failed", error=str(exc))


async def _live_run(
    signal: dict, user_id: str, leg: dict, exec_id: str, api_key: str, jwt: str
) -> None:
    from core.angel.orders import place_order_angel

    strategy_name = signal.get("strategy_name") or (signal.get("payload") or {}).get("strategy_name", "")

    sent_at = datetime.now(IST)
    t0 = time.perf_counter()

    try:
        resp = await place_order_angel(
            api_key=api_key, jwt=jwt,
            tradingsymbol=leg["symbol"], symboltoken=leg["token"],
            side=leg["side"], quantity=leg["qty"],
            exchange=signal.get("payload", {}).get("order_exchange", "NFO"),
        )
        completed_at = datetime.now(IST)
        duration_ms = (time.perf_counter() - t0) * 1000

        if resp.get("status"):
            order_id = resp.get("data", {}).get("orderid", "")
            await asyncio.to_thread(update_execution, exec_id, status="placed", order_id=order_id)
            msg = _notification_message(signal, leg)
            await asyncio.to_thread(notify_user, user_id, leg["side"], msg)
            logger.info(
                "[LIVE] user=%s leg=%s PLACED order_id=%s: %s",
                user_id[:8], leg["leg"], order_id, msg,
            )
            order_timing_logger.info(
                "user=%s strategy=%s leg=%s side=%s symbol=%s sent_at=%s completed_at=%s "
                "duration_ms=%.1f result=PLACED order_id=%s",
                user_id, strategy_name, leg["leg"], leg["side"], leg["symbol"],
                sent_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                completed_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                duration_ms, order_id,
            )
        else:
            err = resp.get("message", "Unknown error")
            await asyncio.to_thread(update_execution, exec_id, status="failed", error=err)
            logger.error(
                "[LIVE] user=%s leg=%s ORDER REJECTED by broker: %s | symbol=%s token=%s qty=%s exchange=%s",
                user_id[:8], leg["leg"], err, leg["symbol"], leg["token"], leg["qty"],
                signal.get("payload", {}).get("order_exchange", "NFO"),
            )
            order_timing_logger.info(
                "user=%s strategy=%s leg=%s side=%s symbol=%s sent_at=%s completed_at=%s "
                "duration_ms=%.1f result=REJECTED error=%s",
                user_id, strategy_name, leg["leg"], leg["side"], leg["symbol"],
                sent_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                completed_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
                duration_ms, err,
            )

    except Exception as exc:
        completed_at = datetime.now(IST)
        duration_ms = (time.perf_counter() - t0) * 1000
        await asyncio.to_thread(update_execution, exec_id, status="failed", error=str(exc))
        logger.error(
            "[LIVE] user=%s leg=%s EXCEPTION before/while placing order: %s",
            user_id[:8], leg["leg"], exc, exc_info=True,
        )
        order_timing_logger.info(
            "user=%s strategy=%s leg=%s side=%s symbol=%s sent_at=%s completed_at=%s "
            "duration_ms=%.1f result=EXCEPTION error=%s",
            user_id, strategy_name, leg["leg"], leg["side"], leg["symbol"],
            sent_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            completed_at.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3],
            duration_ms, exc,
        )
