"""Wrapper around the Coinbase Advanced Trade REST API.

Uses the official ``coinbase-advanced-py`` SDK for authentication and request
signing.  All trading operations (fetch candles, get balances, place / cancel
orders) are centralised here so the rest of the bot is exchange-agnostic.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import config

logger = logging.getLogger(__name__)

# Granularity string → approximate seconds (used to calculate start time)
_GRAN_SECONDS: dict[str, int] = {
    "ONE_MINUTE": 60,
    "FIVE_MINUTE": 300,
    "FIFTEEN_MINUTE": 900,
    "THIRTY_MINUTE": 1800,
    "ONE_HOUR": 3600,
    "TWO_HOUR": 7200,
    "SIX_HOUR": 21600,
    "ONE_DAY": 86400,
}


class CoinbaseClient:
    """Thin wrapper around RESTClient that surfaces only what the bot needs."""

    def __init__(self, api_key: str, api_secret: str) -> None:
        try:
            from coinbase.rest import RESTClient  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "Install coinbase-advanced-py: pip install coinbase-advanced-py"
            ) from exc

        self._client = RESTClient(api_key=api_key, api_secret=api_secret)
        logger.info("CoinbaseClient initialised (dry_run=%s)", config.DRY_RUN)

    # ── Market data ────────────────────────────────────────────────────────────

    def get_candles(
        self,
        product_id: str,
        granularity: str,
        n_candles: int,
    ) -> list[dict[str, Any]]:
        """Return the last *n_candles* OHLCV candles as a list of dicts.

        Each dict has keys: ``start``, ``open``, ``high``, ``low``, ``close``,
        ``volume``.  The list is ordered oldest-first.
        """
        gran_secs = _GRAN_SECONDS.get(granularity, 3600)
        end = int(time.time())
        start = end - gran_secs * (n_candles + 5)  # +5 buffer for API quirks

        response = self._client.get_candles(
            product_id=product_id,
            start=str(start),
            end=str(end),
            granularity=granularity,
        )

        candle_objects = response.candles or []
        # API returns newest-first; reverse to oldest-first
        raw = sorted(candle_objects, key=lambda c: int(c["start"]))
        # Normalise to float values
        candles = [
            {
                "start": int(c["start"]),
                "open": float(c["open"]),
                "high": float(c["high"]),
                "low": float(c["low"]),
                "close": float(c["close"]),
                "volume": float(c["volume"]),
            }
            for c in raw
        ]
        return candles[-n_candles:]  # trim to requested length

    def get_ticker(self, product_id: str) -> dict[str, Any]:
        """Return the best bid/ask/last price for *product_id*."""
        response = self._client.get_best_bid_ask(product_ids=[product_id])
        pricebooks = response.pricebooks or []
        if not pricebooks:
            raise ValueError(f"No ticker data returned for {product_id}")
        pb = pricebooks[0]
        best_bid = float(pb["bids"][0]["price"]) if pb["bids"] else None
        best_ask = float(pb["asks"][0]["price"]) if pb["asks"] else None
        mid = (best_bid + best_ask) / 2 if best_bid and best_ask else None
        return {"bid": best_bid, "ask": best_ask, "mid": mid}

    # ── Account ────────────────────────────────────────────────────────────────

    def get_balance(self, currency: str) -> float:
        """Return the available balance for *currency* (e.g. ``"USD"`` or ``"BTC"``).

        In dry-run mode a simulated balance of ``config.DRY_RUN_BALANCE`` is
        returned immediately without making an authenticated API call.
        """
        if config.DRY_RUN:
            return config.DRY_RUN_BALANCE
        response = self._client.get_accounts()
        accounts = response.accounts or []
        for account in accounts:
            if account["currency"] == currency:
                balance = account["available_balance"]
                return float(balance["value"] if balance else 0)
        return 0.0

    # ── Orders ─────────────────────────────────────────────────────────────────

    def place_market_buy(self, product_id: str, quote_size: float) -> dict[str, Any]:
        """Place a market buy order spending *quote_size* of the quote currency."""
        if config.DRY_RUN:
            logger.info(
                "[DRY RUN] MARKET BUY %s  quote_size=%.4f", product_id, quote_size
            )
            return {"dry_run": True, "side": "BUY", "quote_size": quote_size}

        order_config = {
            "market_market_ioc": {"quote_size": f"{quote_size:.2f}"}
        }
        response = self._client.create_order(
            client_order_id=_unique_order_id("buy"),
            product_id=product_id,
            side="BUY",
            order_configuration=order_config,
        )
        logger.info("Market BUY placed: %s", response)
        return response

    def place_market_sell(
        self, product_id: str, base_size: float
    ) -> dict[str, Any]:
        """Place a market sell order for *base_size* units of the base currency."""
        if config.DRY_RUN:
            logger.info(
                "[DRY RUN] MARKET SELL %s  base_size=%.8f", product_id, base_size
            )
            return {"dry_run": True, "side": "SELL", "base_size": base_size}

        order_config = {
            "market_market_ioc": {"base_size": f"{base_size:.8f}"}
        }
        response = self._client.create_order(
            client_order_id=_unique_order_id("sell"),
            product_id=product_id,
            side="SELL",
            order_configuration=order_config,
        )
        logger.info("Market SELL placed: %s", response)
        return response

    def place_limit_buy(
        self,
        product_id: str,
        base_size: float,
        limit_price: float,
        post_only: bool = True,
    ) -> dict[str, Any]:
        """Place a GTC limit buy order."""
        if config.DRY_RUN:
            logger.info(
                "[DRY RUN] LIMIT BUY %s  size=%.8f @ %.4f",
                product_id,
                base_size,
                limit_price,
            )
            return {
                "dry_run": True,
                "side": "BUY",
                "base_size": base_size,
                "limit_price": limit_price,
            }

        order_config = {
            "limit_limit_gtc": {
                "base_size": f"{base_size:.8f}",
                "limit_price": f"{limit_price:.4f}",
                "post_only": post_only,
            }
        }
        response = self._client.create_order(
            client_order_id=_unique_order_id("lbuy"),
            product_id=product_id,
            side="BUY",
            order_configuration=order_config,
        )
        logger.info("Limit BUY placed: %s", response)
        return response

    def place_limit_sell(
        self,
        product_id: str,
        base_size: float,
        limit_price: float,
        post_only: bool = True,
    ) -> dict[str, Any]:
        """Place a GTC limit sell order."""
        if config.DRY_RUN:
            logger.info(
                "[DRY RUN] LIMIT SELL %s  size=%.8f @ %.4f",
                product_id,
                base_size,
                limit_price,
            )
            return {
                "dry_run": True,
                "side": "SELL",
                "base_size": base_size,
                "limit_price": limit_price,
            }

        order_config = {
            "limit_limit_gtc": {
                "base_size": f"{base_size:.8f}",
                "limit_price": f"{limit_price:.4f}",
                "post_only": post_only,
            }
        }
        response = self._client.create_order(
            client_order_id=_unique_order_id("lsell"),
            product_id=product_id,
            side="SELL",
            order_configuration=order_config,
        )
        logger.info("Limit SELL placed: %s", response)
        return response


# ── Helpers ────────────────────────────────────────────────────────────────────

def _unique_order_id(prefix: str) -> str:
    return f"{prefix}-{int(time.time() * 1000)}"
