"""
patterns/engulfing.py
---------------------
BullishEngulfing — detects a two-candle reversal pattern where a green
(bullish) candle's real body completely engulfs the prior red (bearish)
candle's real body.

No Pandas. Pure Python math only for maximum throughput in the hot loop.
"""

from .base import BasePattern


class BullishEngulfing(BasePattern):
    """
    Fires when **all three** conditions hold simultaneously:

    1. Previous candle is **bearish** : close[-2] < open[-2]
    2. Current candle is **bullish**  : close[-1] > open[-1]
    3. Current body **engulfs** previous body:
           current open  ≤ previous close   (opens below prior close)
         AND
           current close ≥ previous open    (closes above prior open)

    Requires at least 2 candles in the window.  Returns False if the
    window is too small, so the consumer loop never raises an exception.
    """

    def __init__(self):
        super().__init__(name="BullishEngulfing")

    # ------------------------------------------------------------------
    def evaluate(
        self,
        opens: list,
        highs: list,
        lows: list,
        closes: list,
        volumes: list,
    ) -> bool:
        """
        Parameters mirror BasePattern.evaluate().
        Newest candle is at index -1 (last element).
        """
        # Guard: need at least 2 candles (previous + current)
        if len(opens) < 2 or len(closes) < 2:
            return False

        # ── Previous candle ──────────────────────────────────────────
        prev_open: float = opens[-2]
        prev_close: float = closes[-2]

        # ── Current candle ───────────────────────────────────────────
        curr_open: float = opens[-1]
        curr_close: float = closes[-1]

        # ── Pattern conditions ───────────────────────────────────────
        prev_bearish: bool = prev_close < prev_open
        curr_bullish: bool = curr_close > curr_open

        # Current body fully engulfs previous body
        curr_engulfs_prev: bool = (
            curr_open <= prev_close   # opens at or below prior close
            and curr_close >= prev_open  # closes at or above prior open
        )

        return prev_bearish and curr_bullish and curr_engulfs_prev
