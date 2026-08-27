"""Write user-facing notifications to Supabase.

Used by both the dry-run path (signal logging) and the live executor.
Synchronous (supabase-py is sync); from async code call it via
`await asyncio.to_thread(add_notification, ...)`.
"""

from __future__ import annotations

from core.supabase.client import get_client

# caller's notification_type -> notifications.type DB value
_TYPE_MAP = {
    "BUY":   "trade",
    "SELL":  "trade",
    "INFO":  "info",
    "ERROR": "error",
    "ALERT": "alert",
}

def notify_user(user_id: str, notification_type: str, message: str) -> None:
    db_type = _TYPE_MAP.get(notification_type.upper(), "info")
    get_client().table("notifications").insert(
        {"client_id": user_id, "type": db_type, "message": message}
    ).execute()