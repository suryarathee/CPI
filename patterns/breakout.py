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
        # Guard: need at least 50 candles for SMA 50 and LOOKBACK
        if len(closes) < 50 or len(volumes) < 50 or len(highs) < 50 or len(lows) < 50:
            return False

        # ── Trend Filter (SMA 50) ────────────────────────────────────
        sma_50 = sum(closes[-50:]) / 50.0
        
        # ── Current candle (index -1) ────────────────────────────────
        current_close: float = closes[-1]
        current_high: float = highs[-1]
        current_low: float = lows[-1]
        current_volume: float = volumes[-1]

        # Breakout trend filter
        if current_close <= sma_50:
            return False

        # ── Previous LOOKBACK candles (indices -(LOOKBACK+1) to -2) ──
        prev_highs: list = highs[-(LOOKBACK + 1):-1]
        prev_volumes: list = volumes[-(LOOKBACK + 1):-1]

        resistance: float = max(prev_highs)
        avg_volume: float = sum(prev_volumes) / len(prev_volumes) if prev_volumes else 1.0

        # ── Pattern conditions ───────────────────────────────────────
        price_breakout: bool = current_close > resistance
        volume_confirmed: bool = current_volume > VOLUME_MULTIPLIER * avg_volume
        
        # Stricter: must close in upper 25% of candle range
        range_size = current_high - current_low
        close_strength = (current_close - current_low) / range_size if range_size > 0 else 0
        strong_close: bool = close_strength >= 0.75

        return price_breakout and volume_confirmed and strong_close
