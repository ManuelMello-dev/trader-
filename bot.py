"""Main 24/7 trading bot runner.

Usage
─────
Copy .env.example to .env and fill in your Coinbase API credentials, then:

    python bot.py

The bot will run indefinitely, evaluating the market every
``BOT_INTERVAL_SECONDS`` seconds (default 300 s / 5 minutes).

Set ``DRY_RUN=true`` (the default) to simulate without placing real orders.
Set ``DRY_RUN=false`` only when you are ready to trade live.

Architecture
────────────
1. Fetch latest OHLCV candles from Coinbase.
2. Run technical analysis (pivots, S/R, structure, RSI, ATR).
3. Ask the strategy module for a decision (BUY / SELL / HOLD).
4. Execute the decision via the Coinbase client.
5. Log the trade and update performance metrics for adaptive sizing.
6. Sleep until the next interval.
"""

from __future__ import annotations

import logging
import signal
import time

import config
import learning
from coinbase_client import CoinbaseClient
from strategy import Decision, Position, evaluate
from technical_analysis import Candle

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger("bot")


# ── Graceful shutdown ──────────────────────────────────────────────────────────
_running = True


def _handle_signal(signum: int, _frame: object) -> None:
    global _running
    logger.info("Signal %d received – shutting down after current cycle.", signum)
    _running = False


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ── Bot state ──────────────────────────────────────────────────────────────────

class BotState:
    """Holds mutable runtime state for the bot."""

    def __init__(self) -> None:
        self.position = Position()
        self.entry_time: int = 0
        self.active_pair: str = config.TRADING_PAIR

        # Adaptive parameters (updated after each trade)
        self.win_rate: float = 0.5
        self.avg_win: float = 0.01
        self.avg_loss: float = 0.01

    def refresh_metrics(self) -> None:
        """Reload trade history and recalculate performance metrics."""
        trades = learning.load_trades()
        metrics = learning.compute_metrics(trades)
        self.win_rate = metrics["win_rate"]
        self.avg_win = metrics["avg_win"]
        self.avg_loss = metrics["avg_loss"]

        params = learning.suggest_parameters(metrics)
        if params:
            # Apply suggestions by monkeypatching the config module so all
            # modules see the updated values without a restart.
            for key, value in params.items():
                attr = key.upper()
                if hasattr(config, attr):
                    setattr(config, attr, value)
                    logger.info("Config updated: %s = %.4f", attr, value)


# ── Main loop ──────────────────────────────────────────────────────────────────

def run() -> None:
    """Entry point – runs the bot loop until interrupted."""
    pair_display = "AUTO" if config.AUTO_SELECT_PAIR else config.TRADING_PAIR
    logger.info(
        "Starting trader bot  pair=%s  dry_run=%s  interval=%ds",
        pair_display,
        config.DRY_RUN,
        config.BOT_INTERVAL_SECONDS,
    )

    if not config.DRY_RUN and (not config.API_KEY or not config.API_SECRET):
        logger.error(
            "COINBASE_API_KEY and COINBASE_API_SECRET must be set in the .env file."
        )
        return

    client = CoinbaseClient(api_key=config.API_KEY, api_secret=config.API_SECRET)
    state = BotState()
    state.refresh_metrics()

    while _running:
        cycle_start = time.time()
        try:
            _run_cycle(client, state)
        except Exception as exc:  # pylint: disable=broad-except
            # Log full traceback; keep the bot alive for transient errors.
            # Authentication failures (401/403) will appear here and require
            # manual intervention to fix credentials.
            logger.exception("Unhandled error in cycle: %s", exc)

        elapsed = time.time() - cycle_start
        sleep_for = max(0, config.BOT_INTERVAL_SECONDS - elapsed)
        logger.debug("Cycle done in %.1fs. Sleeping %.1fs.", elapsed, sleep_for)
        _sleep(sleep_for)

    logger.info("Bot stopped.")


def _run_cycle(client: CoinbaseClient, state: BotState) -> None:
    """Execute one evaluation cycle."""
    # ── 0. Auto-select best pair when flat ─────────────────────────────────────
    if config.AUTO_SELECT_PAIR and not state.position.is_open:
        state.active_pair = _select_pair(client, state)

    # ── 1. Fetch candles ───────────────────────────────────────────────────────
    raw_candles = client.get_candles(
        product_id=state.active_pair,
        granularity=config.CANDLE_GRANULARITY,
        n_candles=config.LOOKBACK_CANDLES,
    )
    if not raw_candles:
        logger.warning("No candle data received. Skipping cycle.")
        return

    candles = [Candle(**c) for c in raw_candles]
    price = candles[-1].close
    logger.info(
        "Cycle  pair=%s  price=%.4f  candles=%d  position=%s",
        state.active_pair,
        price,
        len(candles),
        "OPEN" if state.position.is_open else "FLAT",
    )

    # ── 2. Strategy decision ───────────────────────────────────────────────────
    decision: Decision = evaluate(
        candles=candles,
        position=state.position,
        win_rate=state.win_rate,
        avg_win=state.avg_win,
        avg_loss=state.avg_loss,
    )
    logger.info(
        "Decision: %s  reason=%s", decision.action, decision.reason
    )

    # ── 3. Execute ─────────────────────────────────────────────────────────────
    if decision.action == "BUY" and not state.position.is_open:
        _execute_buy(client, state, decision, price)

    elif decision.action == "SELL" and state.position.is_open:
        _execute_sell(client, state, decision, price)


def _execute_buy(
    client: CoinbaseClient,
    state: BotState,
    decision: Decision,
    price: float,
) -> None:
    """Calculate position size and place a buy order."""
    quote_balance = client.get_balance(config.QUOTE_CURRENCY)
    quote_to_spend = quote_balance * decision.position_size_pct

    if quote_to_spend < 1.0:
        logger.warning(
            "Insufficient balance (%.2f %s) to open position.",
            quote_balance,
            config.QUOTE_CURRENCY,
        )
        return

    result = client.place_market_buy(
        product_id=state.active_pair,
        quote_size=quote_to_spend,
    )

    base_size = quote_to_spend / price  # approximate; real fill may differ
    state.position = Position(
        entry_price=price,
        base_size=base_size,
        stop_loss=decision.stop_loss,
        take_profit=decision.take_profit,
        is_open=True,
    )
    state.entry_time = int(time.time())
    logger.info(
        "Opened LONG  entry=%.4f  SL=%.4f  TP=%.4f  size=%.6f  result=%s",
        price,
        decision.stop_loss,
        decision.take_profit,
        base_size,
        result,
    )


def _execute_sell(
    client: CoinbaseClient,
    state: BotState,
    decision: Decision,
    price: float,
) -> None:
    """Close the open position."""
    result = client.place_market_sell(
        product_id=state.active_pair,
        base_size=state.position.base_size,
    )

    pnl_pct = (price - state.position.entry_price) / state.position.entry_price

    record = learning.TradeRecord(
        product_id=state.active_pair,
        side="SELL",
        entry_price=state.position.entry_price,
        exit_price=price,
        base_size=state.position.base_size,
        entry_time=state.entry_time,
        exit_time=int(time.time()),
        stop_loss=state.position.stop_loss,
        take_profit=state.position.take_profit,
        pnl_pct=pnl_pct,
        reason=decision.reason,
        dry_run=config.DRY_RUN,
    )
    learning.save_trade(record)

    logger.info(
        "Closed LONG  exit=%.4f  PnL=%.2f%%  reason=%s  result=%s",
        price,
        pnl_pct * 100,
        decision.reason,
        result,
    )

    state.position = Position()
    state.entry_time = 0
    state.refresh_metrics()


def _score_opportunity(decision: Decision, price: float) -> float:
    """Return an opportunity score for a trading pair (higher = better entry).

    Only BUY signals receive a positive score.  The score combines the
    reward-to-risk ratio with relative volatility (ATR as a % of price) so
    that high-quality set-ups on volatile pairs rank first.
    """
    if decision.action != "BUY":
        return 0.0
    risk = price - decision.stop_loss
    if risk <= 0 or price <= 0:
        return 0.0
    rr = (decision.take_profit - price) / risk
    atr_pct = decision.atr / price
    return rr * atr_pct


def _select_pair(client: CoinbaseClient, state: BotState) -> str:
    """Evaluate every candidate pair and return the one with the best entry.

    Falls back to ``state.active_pair`` when no pair produces a BUY signal or
    when all candidate fetches fail.
    """
    best_pair = state.active_pair
    best_score = -1.0

    for pair in config.CANDIDATE_PAIRS:
        try:
            raw = client.get_candles(
                product_id=pair,
                granularity=config.CANDLE_GRANULARITY,
                n_candles=config.LOOKBACK_CANDLES,
            )
            if not raw:
                continue
            candles = [Candle(**c) for c in raw]
            decision = evaluate(
                candles=candles,
                position=Position(),
                win_rate=state.win_rate,
                avg_win=state.avg_win,
                avg_loss=state.avg_loss,
            )
            price = candles[-1].close
            score = _score_opportunity(decision, price)
            logger.debug(
                "Pair scan  pair=%s  action=%s  score=%.6f",
                pair,
                decision.action,
                score,
            )
            if score > best_score:
                best_score = score
                best_pair = pair
        except Exception as exc:  # pylint: disable=broad-except
            logger.warning("Skipping pair %s during scan: %s", pair, exc)

    if best_pair != state.active_pair:
        logger.info(
            "Auto-selected pair: %s  score=%.6f", best_pair, best_score
        )
    return best_pair


def _sleep(seconds: float) -> None:
    """Sleep in short increments so SIGINT is handled promptly."""
    end = time.time() + seconds
    while _running and time.time() < end:
        time.sleep(min(1.0, end - time.time()))


if __name__ == "__main__":
    run()
