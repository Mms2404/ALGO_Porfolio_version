"""Angel SmartStream websocket feed (LTP / mode 1).

One connection = one feed account. In the new model the DATA feed runs on ADMIN
accounts (the feed pool); the runner wires admin creds + a strategy's tokens
here. tokens and token_meta are supplied by the caller (built via
instruments.build_subscriptions / build_token_meta) -- no hidden NIFTY default.
"""

from __future__ import annotations

import asyncio
import json
import sys

import websockets

from apps.marketdata.parsing import parse_ltp_tick
from core.logging import get_account_logger

WS_URL = "wss://smartapisocket.angelone.in/smart-stream"

if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class AngelWSStream:
    def __init__(self, jwt, feed_token, client_id, api_key, token_ids, on_tick, token_meta, exchange_type=2):
        self.jwt = jwt
        self.feed_token = feed_token
        self.client_id = client_id
        self.api_key = api_key
        self.token_ids = token_ids
        self.on_tick_callback = on_tick
        self.token_map = token_meta or {}
        self.exchange_type = exchange_type  # 1=NSE, 2=NFO

        self.logger = get_account_logger(client_id)
        self.ws = None
        self._running = True

        self.heartbeat_interval = 20
        self.read_timeout = 40
        self.reconnect_delay = 2

    async def connect(self) -> bool:
        headers = {
            "x-client-code": self.client_id,
            "x-feed-token": self.feed_token,
            "Authorization": f"Bearer {self.jwt}",
            "x-api-key": self.api_key,
        }
        try:
            self.ws = await websockets.connect(
                WS_URL, additional_headers=headers, ping_interval=None, ping_timeout=None
            )
            self.logger.info("WebSocket connected.")
            return True
        except Exception as e:
            self.logger.error("WebSocket connect failed: %s", e)
            return False

    async def send_subscription(self):
        req = {
            "correlationID": "token_sub",
            "action": 1,
            "params": {
                "mode": 1,  # LTP
                "tokenList": [{"exchangeType": self.exchange_type, "tokens": self.token_ids}],
            },
        }
        await self.ws.send(json.dumps(req))
        self.logger.info("Subscribed to %d tokens", len(self.token_ids))

    async def send_heartbeat(self):
        while self._running:
            try:
                if self.ws:
                    await self.ws.send("ping")
            except Exception as e:
                self.logger.warning("Heartbeat failed: %s", e)
                return
            await asyncio.sleep(self.heartbeat_interval)

    async def read_messages(self):
        while self._running:
            try:
                msg = await asyncio.wait_for(self.ws.recv(), timeout=self.read_timeout)
            except asyncio.TimeoutError:
                self.logger.warning("WS read timeout — reconnecting")
                return
            except websockets.ConnectionClosed:
                self.logger.warning("WS closed — reconnecting")
                return
            except Exception as e:
                self.logger.error("Unexpected WS error: %s", e)
                return

            # text frames: ACK / error / pong
            if isinstance(msg, str):
                try:
                    self.logger.debug("WS TEXT: %s", json.loads(msg))
                except Exception:
                    self.logger.debug("WS TEXT (non-JSON): %s", msg)
                continue

            # binary frames: LTP tick
            if not isinstance(msg, (bytes, bytearray)):
                continue
            tick = parse_ltp_tick(msg)
            if not tick:
                continue
            meta = self.token_map.get(int(tick["token"]))
            if meta is None:
                continue  # tick for a token we have no metadata for
            tick["symbol_info"] = meta
            try:
                await self.on_tick_callback(tick)
            except Exception as e:
                self.logger.error("Tick callback error: %s", e)

    async def run_forever(self):
        while self._running:
            if not await self.connect():
                await asyncio.sleep(self.reconnect_delay)
                continue
            await self.send_subscription()

            heartbeat_task = asyncio.create_task(self.send_heartbeat())
            try:
                await self.read_messages()
            finally:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                try:
                    if self.ws:
                        await self.ws.close()
                except Exception:
                    pass

            if self._running:
                self.logger.info("WebSocket stopped. Reconnecting in %ss...", self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)

    def stop(self):
        self._running = False


async def run_market_stream(jwt, feed_token, client_id, api_key, on_tick, *, tokens, token_meta, exchange_type=2):
    """Run a market stream for one feed account. tokens + token_meta are required
    (the caller decides what to subscribe and supplies metadata)."""
    stream = AngelWSStream(jwt, feed_token, client_id, api_key, tokens, on_tick, token_meta, exchange_type)
    await stream.run_forever()
    return stream