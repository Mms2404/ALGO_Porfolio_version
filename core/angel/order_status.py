"""Angel order-status websocket (per-user). Waits for fills/rejections.

LIVE-path only — not used in the dry-run test (no real orders to track).
"""

from __future__ import annotations

import asyncio
import json

import websockets

ORDER_WS_URL = "wss://tns.angelone.in/smart-order-update"


def is_complete(order_status: str) -> bool:
    if not order_status:
        return False
    return order_status.lower() == "complete" or order_status.upper() == "AB05"


def is_rejected(order_status: str) -> bool:
    if not order_status:
        return False
    return order_status.lower() == "rejected" or order_status.upper() == "AB03"


async def wait_for_orders(jwt_token: str, order_ids: list | None = None) -> dict:
    order_ids = order_ids or []
    headers = [("Authorization", f"Bearer {jwt_token}")]

    async with websockets.connect(
        ORDER_WS_URL, additional_headers=headers, ping_interval=None, max_size=None
    ) as ws:
        filled: set[str] = set()

        async def ping_loop():
            try:
                while True:
                    await ws.send("ping")
                    await asyncio.sleep(10)
            except Exception:
                return

        ping_task = asyncio.create_task(ping_loop())
        try:
            while True:
                try:
                    msg = await ws.recv()
                    try:
                        data = json.loads(msg)
                    except Exception:
                        continue

                    order_data = data.get("orderData", {})
                    order_id = str(order_data.get("orderid", ""))
                    order_status = order_data.get("orderstatus", "")

                    # No ids given -> return the first meaningful update.
                    if not order_ids and order_status:
                        return {"order_id": order_id, "status": order_status}

                    if order_id not in order_ids:
                        continue

                    if is_rejected(order_status):
                        return {"status": "rejected", "order_id": order_id}
                    if is_complete(order_status):
                        filled.add(order_id)
                    if all(oid in filled for oid in order_ids):
                        return {"status": "completed"}
                except Exception as exc:
                    print("Order WS error:", exc)
                    await asyncio.sleep(2)
        finally:
            ping_task.cancel()