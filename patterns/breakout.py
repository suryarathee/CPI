"""
patterns/breakout.py
--------------------
VolumeBreakout — detects a price breakout above the prior 49-candle high
that is confirmed by an abnormal volume surge (≥ 2.5× average).

No Pandas. Pure Python math only for maximum throughput in the hot loop.
"""

from .base import BasePattern

# ── Tunable constants ────────────────────────────────────────────────────────
LOOKBACK: int = 49          # number of *previous* candles used as baseline
VOLUME_MULTIPLIER: float = 2.5   # minimum RVOL required to confirm breakout
# ─────────────────────────────────────────────────────────────────────────────


class VolumeBreakout(BasePattern):
    """
    Fires when the newest candle closes above the highest high of the
    preceding ``LOOKBACK`` candles AND prints a volume spike ≥ 2.5×
    the average volume over the same window.

    Expected window size: at least LOOKBACK + 1 candles (50 total).
    If insufficient data is present the pattern returns False instead
    of raising an exception, keeping the consumer loop safe.
    """

    def __init__(self):
        super().__init__(name="VolumeBreakout")

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
        # Guard: need at least LOOKBACK previous candles + 1 current candle
        if len(closes) < LOOKBACK + 1 or len(volumes) < LOOKBACK + 1:
            return False

        # ── Current candle (index -1) ────────────────────────────────
        current_close: float = closes[-1]
        current_volume: float = volumes[-1]

        # ── Previous LOOKBACK candles (indices -(LOOKBACK+1) to -2) ──
        prev_highs: list = highs[-(LOOKBACK + 1):-1]
        prev_volumes: list = volumes[-(LOOKBACK + 1):-1]

        resistance: float = max(prev_highs)
        avg_volume: float = sum(prev_volumes) / len(prev_volumes) if prev_volumes else 1.0

        # ── Pattern conditions ───────────────────────────────────────
        price_breakout: bool = current_close > resistance
        volume_confirmed: bool = current_volume > VOLUME_MULTIPLIER * avg_volume

        return price_breakout and volume_confirmed
