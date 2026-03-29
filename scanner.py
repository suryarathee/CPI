from __future__ import annotations

import importlib
import inspect
import re
import subprocess
from pathlib import Path
from typing import Any

import pandas as pd

from runtime_config import available_symbols


PARQUET_DIR = Path(__file__).resolve().parent / "parquet_data"
DEFAULT_SCAN_COMMANDS = (
    ["pkscreenercli", "-a", "Y", "-p", "-o", "X:12:9:2.5", "-e"],
    ["python", "-m", "pkscreenercli", "-a", "Y", "-p", "-o", "X:12:9:2.5", "-e"],
    ["python", "-m", "pkscreener", "-a", "Y", "-p", "-o", "X:12:9:2.5", "-e"],
)
SYMBOL_REGEX = re.compile(r"\b[A-Z][A-Z0-9&-]{2,14}\b")


def _normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    normalized = df.copy()
    rename_map = {
        "timestamp": "Date",
        "date": "Date",
        "datetime": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    normalized = normalized.rename(columns={key: value for key, value in rename_map.items() if key in normalized.columns})

    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in normalized.columns]
    if missing:
        raise ValueError(f"OHLCV data is missing required columns: {', '.join(missing)}")

    if "Date" not in normalized.columns:
        normalized["Date"] = pd.RangeIndex(start=0, stop=len(normalized))

    normalized["Date"] = pd.to_datetime(normalized["Date"], errors="coerce", utc=True)
    normalized["Date"] = normalized["Date"].ffill().bfill()

    for column in required:
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce")

    normalized = normalized.dropna(subset=["Open", "High", "Low", "Close", "Volume"]).copy()
    return normalized.sort_values("Date").reset_index(drop=True)


def _call_indicator(func: Any, data: pd.DataFrame, **kwargs: Any) -> pd.DataFrame:
    if func is None:
        return data
    try:
        signature = inspect.signature(func)
        accepted = {key: value for key, value in kwargs.items() if key in signature.parameters}
        return func(data, **accepted)
    except Exception:
        return data


def _load_pyindicators() -> Any | None:
    try:
        return importlib.import_module("pyindicators")
    except Exception:
        return None


def enrich_indicator_frame(df: pd.DataFrame) -> pd.DataFrame:
    enriched = _normalize_ohlcv(df)
    indicators = _load_pyindicators()

    if indicators is not None:
        for period in (9, 20, 50):
            enriched = _call_indicator(
                getattr(indicators, "ema", None),
                enriched,
                source_column="Close",
                period=period,
                result_column=f"EMA_{period}",
            )

        enriched = _call_indicator(
            getattr(indicators, "rsi", None),
            enriched,
            source_column="Close",
            period=14,
            result_column="RSI_14",
        )
        enriched = _call_indicator(
            getattr(indicators, "macd", None),
            enriched,
            source_column="Close",
            short_period=12,
            long_period=26,
            signal_period=9,
        )

    close_series = enriched["Close"]
    volume_series = enriched["Volume"]

    if "EMA_9" not in enriched.columns:
        enriched["EMA_9"] = close_series.ewm(span=9, adjust=False).mean()
    if "EMA_20" not in enriched.columns:
        enriched["EMA_20"] = close_series.ewm(span=20, adjust=False).mean()
    if "EMA_50" not in enriched.columns:
        enriched["EMA_50"] = close_series.ewm(span=50, adjust=False).mean()
    if "RSI_14" not in enriched.columns:
        delta = close_series.diff()
        gains = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
        losses = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
        safe_losses = losses.mask(losses == 0)
        rs = gains.divide(safe_losses)
        enriched["RSI_14"] = (100 - (100 / (1 + rs))).astype("float64").fillna(50.0)

    if "MACD_12_26_9" not in enriched.columns:
        ema_fast = close_series.ewm(span=12, adjust=False).mean()
        ema_slow = close_series.ewm(span=26, adjust=False).mean()
        enriched["MACD_12_26_9"] = ema_fast - ema_slow
    if "MACD_SIGNAL_12_26_9" not in enriched.columns:
        enriched["MACD_SIGNAL_12_26_9"] = enriched["MACD_12_26_9"].ewm(span=9, adjust=False).mean()
    if "MACD_HIST_12_26_9" not in enriched.columns:
        enriched["MACD_HIST_12_26_9"] = enriched["MACD_12_26_9"] - enriched["MACD_SIGNAL_12_26_9"]

    enriched["AVG_VOL_20"] = volume_series.rolling(20).mean()
    enriched["ROLLING_HIGH_20"] = enriched["High"].rolling(20).max().shift(1)
    enriched["ROLLING_CLOSE_10"] = close_series.rolling(10).mean()

    # Stricter breakout logic with trend filter
    range_size = enriched["High"] - enriched["Low"]
    close_strength = (enriched["Close"] - enriched["Low"]) / range_size.replace(0, 1)
    enriched["VOLUME_BREAKOUT_SIGNAL"] = (
        (enriched["Close"] > enriched["ROLLING_HIGH_20"])
        & (enriched["Volume"] > (enriched["AVG_VOL_20"] * 2.5))
        & (close_strength >= 0.75)
        & (enriched["Close"] > enriched["EMA_50"])
    )

    enriched["EMA_MOMENTUM_SIGNAL"] = (
        (enriched["EMA_9"] > enriched["EMA_20"])
        & (enriched["EMA_20"] > enriched["EMA_50"])
        & (enriched["Close"] > enriched["EMA_9"])
        & (enriched["RSI_14"] >= 55)
    )
    enriched["RSI_MACD_SIGNAL"] = (
        (enriched["RSI_14"] >= 60)
        & (enriched["MACD_HIST_12_26_9"] > 0)
        & (enriched["Close"] > enriched["ROLLING_CLOSE_10"])
    )

    # Bullish Engulfing Logic (Stricter with trend filter)
    prev_close = enriched["Close"].shift(1)
    prev_open = enriched["Open"].shift(1)
    prev_volume = enriched["Volume"].shift(1)
    curr_close = enriched["Close"]
    curr_open = enriched["Open"]
    curr_volume = enriched["Volume"]

    enriched["BULLISH_ENGULFING_SIGNAL"] = (
        (prev_close < prev_open)  # Prev candle bearish
        & (curr_close > curr_open) # Curr candle bullish
        & (curr_open <= prev_close) # Opens at or below prior close
        & (curr_close >= prev_open) # Closes at or above prior open
        & (curr_volume > prev_volume) # Volume confirmation
        & (curr_close > enriched["EMA_50"]) # Trend filter
    )

    return enriched


def pattern_signal_mask(df: pd.DataFrame, pattern_name: str) -> pd.Series:
    enriched = enrich_indicator_frame(df)
    signal_map = {
        "VolumeBreakout": "VOLUME_BREAKOUT_SIGNAL",
        "EMAMomentum": "EMA_MOMENTUM_SIGNAL",
        "RSIMACDContinuation": "RSI_MACD_SIGNAL",
        "BullishEngulfing": "BULLISH_ENGULFING_SIGNAL",
    }
    signal_column = signal_map.get(pattern_name)
    if signal_column is None:
        raise ValueError(f"Unsupported pattern for historical edge run: {pattern_name}")
    return enriched[signal_column].fillna(False).astype(bool)


def collect_indicator_context(symbol: str, live_df: pd.DataFrame) -> dict[str, Any] | None:
    enriched = enrich_indicator_frame(live_df)
    if enriched.empty:
        return None

    last_row = enriched.iloc[-1]
    checks = (
        ("VolumeBreakout", bool(last_row.get("VOLUME_BREAKOUT_SIGNAL", False))),
        ("EMAMomentum", bool(last_row.get("EMA_MOMENTUM_SIGNAL", False))),
        ("RSIMACDContinuation", bool(last_row.get("RSI_MACD_SIGNAL", False))),
    )
    triggered = next((name for name, fired in checks if fired), None)
    if triggered is None:
        return None

    return {
        "symbol": symbol,
        "pattern_name": triggered,
        "close": float(last_row["Close"]),
        "volume": float(last_row["Volume"]),
        "ema_9": float(last_row["EMA_9"]),
        "ema_20": float(last_row["EMA_20"]),
        "ema_50": float(last_row["EMA_50"]),
        "rsi_14": float(last_row["RSI_14"]),
        "macd_hist": float(last_row["MACD_HIST_12_26_9"]),
        "avg_vol_20": float(last_row["AVG_VOL_20"]) if pd.notna(last_row["AVG_VOL_20"]) else 0.0,
        "timestamp": last_row["Date"].isoformat() if hasattr(last_row["Date"], "isoformat") else str(last_row["Date"]),
    }


def confirm_pattern(symbol: str, live_df: pd.DataFrame) -> str | None:
    context = collect_indicator_context(symbol, live_df)
    return context["pattern_name"] if context else None


def _parse_pkscreener_output(stdout: str) -> list[str]:
    discovered = []
    valid_symbols = set(available_symbols())
    for token in SYMBOL_REGEX.findall(stdout.upper()):
        if token in valid_symbols and token not in discovered:
            discovered.append(token)
    return discovered


def _scan_with_pkscreener() -> list[str]:
    for command in DEFAULT_SCAN_COMMANDS:
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=45,
            )
        except Exception:
            continue

        symbols = _parse_pkscreener_output((completed.stdout or "") + "\n" + (completed.stderr or ""))
        if symbols:
            return symbols
    return []


def _fallback_local_scan(max_symbols: int = 12) -> list[str]:
    scored: list[tuple[float, str]] = []
    for symbol in available_symbols():
        parquet_path = PARQUET_DIR / f"{symbol}.parquet"
        if not parquet_path.exists():
            continue

        try:
            df = pd.read_parquet(parquet_path).tail(120)
            context = collect_indicator_context(symbol, df)
        except Exception:
            continue

        if not context:
            continue

        score = context["rsi_14"] + max(context["macd_hist"], 0.0)
        if context["avg_vol_20"] > 0:
            score += (context["volume"] / context["avg_vol_20"]) * 10
        scored.append((score, symbol))

    scored.sort(reverse=True)
    return [symbol for _score, symbol in scored[:max_symbols]]


def scan_nse_universe() -> list[str]:
    symbols = _scan_with_pkscreener()
    if symbols:
        return symbols
    return _fallback_local_scan()
