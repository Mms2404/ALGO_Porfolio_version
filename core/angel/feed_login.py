"""Feed-account login with fallback.

Every strategy needs a logged-in FEED account to pull market data. Rather
than hardcoding a single FEED_ACCOUNTS[i] and failing the whole strategy
run if that account's login is rejected (rate limit, temporary block,
etc.), this tries each configured feed account in turn -- starting from
the strategy's usual preferred index -- and returns the first one that
logs in successfully.

This only covers the FEED (market-data) login. It has nothing to do with
a user's own Angel trading account, which is handled separately in
apps/execution/executor.py.
"""

from __future__ import annotations

import logging

from core.angel.auth import login_to_angel
from core.angel.feed_accounts import FEED_ACCOUNTS

logger = logging.getLogger(__name__)


def login_feed_account(preferred_index: int = 0) -> tuple[dict, dict] | None:
    """Try FEED_ACCOUNTS starting at preferred_index, wrapping around to
    the rest if that one fails to log in.

    Returns (creds, login_res) for the first account that logs in
    successfully, where login_res["data"] has jwt_token/feed_token/
    refresh_token -- same shape login_to_angel() already returns.
    Returns None if every configured feed account failed to log in.
    """
    n = len(FEED_ACCOUNTS)
    if n == 0:
        logger.error("FEED_ACCOUNTS is empty — no feed account to log in with")
        return None

    order = [(preferred_index + i) % n for i in range(n)]
    last_error = None

    for idx in order:
        creds = FEED_ACCOUNTS[idx]
        login_res = login_to_angel(
            client_code=creds["client_id"], password=creds["password"],
            totp_secret=creds["totp_secret"], api_key=creds["api_key"],
        )
        if login_res.get("status"):
            if idx != preferred_index:
                logger.warning(
                    "Feed account index %d unavailable — fell back to index %d (%s)",
                    preferred_index, idx, creds.get("client_id"),
                )
            return creds, login_res

        last_error = login_res.get("message")
        logger.warning(
            "Feed account index %d (%s) login failed: %s — trying next",
            idx, creds.get("client_id"), last_error,
        )

    logger.error("All %d feed account(s) failed to log in. Last error: %s", n, last_error)
    return None
