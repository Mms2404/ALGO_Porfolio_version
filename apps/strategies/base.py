"""Base class and registry for all trading strategies.

Each strategy must:
  - Inherit BaseStrategy
  - Implement on_tick(tick: dict)
  - Implement get_subscription_tokens() -> (token_list, token_meta, exchange_type)
  - Use @register_strategy("name") decorator
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple, Type


class BaseStrategy(ABC):
    """
    Abstract base for all strategies in this Django project.

    Subclasses live in apps/strategies/ and are wired to Celery tasks
    in apps/scheduling/tasks.py.

    The on_tick / get_subscription_tokens pattern mirrors the demo project
    so future migrations are copy-paste compatible.
    """

    STRATEGY_NAME = "base"

    def __init__(
        self,
        jwt_token: str,
        feed_token: str,
        client_id: str,
        api_key: str,
        loop=None,
    ):
        self.jwt        = jwt_token
        self.feed_token = feed_token
        self.client_id  = client_id
        self.api_key    = api_key
        self.loop       = loop or asyncio.get_event_loop()
        self.ws         = None
        self._lock      = asyncio.Lock()
        self.logger     = logging.getLogger(f"strategy.{self.STRATEGY_NAME}.{client_id}")

    @abstractmethod
    async def on_tick(self, tick: dict) -> None:
        """Called for every market tick — must be implemented by subclass."""

    def get_subscription_tokens(self) -> Tuple[Optional[list], Optional[dict], int]:
        """
        Returns (token_ids, token_meta, exchange_type).
          exchange_type: 1=NSE (index spot), 2=NFO (options/futures)
        Override to supply strategy-specific tokens.
        """
        return None, None, 2

    async def start(self) -> None:
        """Hook called just before the WebSocket stream starts."""
        self.logger.info("%s started for %s", self.STRATEGY_NAME, self.client_id)

    async def stop(self) -> None:
        """Hook called when the strategy finishes."""
        self.logger.info("%s stopped for %s", self.STRATEGY_NAME, self.client_id)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

STRATEGY_REGISTRY: Dict[str, Type[BaseStrategy]] = {}


def register_strategy(name: str):
    """Class decorator — registers a strategy under `name`."""
    def decorator(cls: Type[BaseStrategy]):
        STRATEGY_REGISTRY[name] = cls
        cls.STRATEGY_NAME = name
        return cls
    return decorator


def get_strategy_class(name: str) -> Optional[Type[BaseStrategy]]:
    return STRATEGY_REGISTRY.get(name)
