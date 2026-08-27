"""Signal creation and execution recording.

Idempotency
-----------
signals           : UNIQUE (strategy_id, trading_day, kind, instrument)
signal_executions : UNIQUE (signal_id, user_id, leg)

create_signal()   returns None if already exists (safe to call again).
record_execution() returns None if already exists (safe to call again).
"""

from __future__ import annotations

import logging
from datetime import date

from core.supabase.client import get_client

logger = logging.getLogger(__name__)


def create_signal(
    strategy_id: int,
    trading_day: date,
    kind: str,
    instrument: str,
    payload: dict | None = None,
) -> dict | None:
    try:
        resp = (
            get_client()
            .table("signals")
            .insert({
                "strategy_id":  strategy_id,
                "trading_day":  trading_day.isoformat(),
                "kind":         kind,
                "instrument":   instrument,
                "payload":      payload or {},
                "status":       "created",
            })
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            logger.info("Signal already exists: %s %s %s", strategy_id, kind, instrument)
            return None
        raise


def record_execution(
    signal_id: str, user_id: str, leg: str, side: str, *,
    symbol: str | None = None,
    token: str | None = None,
    qty: int | None = None,
    price: float | None = None,
    status: str = "pending",
) -> dict | None:
    """Insert a per-user execution row. Returns the row, or None if duplicate."""
    try:
        resp = (
            get_client()
            .table("signal_executions")
            .insert({
                "signal_id": signal_id,
                "user_id":   user_id,
                "leg":       leg,
                "side":      side,
                "symbol":    symbol,
                "token":     token,
                "qty":       qty,
                "price":     price,
                "status":    status,
            })
            .execute()
        )
        return resp.data[0] if resp.data else None
    except Exception as exc:
        if "duplicate" in str(exc).lower() or "unique" in str(exc).lower():
            return None
        raise


def update_execution(
    execution_id: str, *,
    status: str,
    order_id: str | None = None,
    error: str | None = None,
) -> None:
    update: dict = {"status": status}
    if order_id:
        update["order_id"] = order_id
    if error:
        update["error"] = error
    get_client().table("signal_executions").update(update).eq("id", execution_id).execute()
