from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


PARQUET_DIR = Path(__file__).resolve().parent / "parquet_data"
DEFAULT_WINDOW_SIZE = 250


def _load_symbol_history(symbol: str) -> pd.DataFrame:
    parquet_path = PARQUET_DIR / f"{symbol}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"Historical parquet not found for {symbol}: {parquet_path}")

    df = pd.read_parquet(parquet_path).copy()
    required_columns = {"open", "high", "low", "close", "volume"}
    missing = required_columns.difference(df.columns)
    if missing:
        missing_csv = ", ".join(sorted(missing))
        raise ValueError(f"Parquet file for {symbol} is missing columns: {missing_csv}")

    if "timestamp" in df.columns:
        df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
        df = df.sort_values("timestamp")

    return df.reset_index(drop=True)


def _resolve_window_size(pattern_obj: Any) -> int:
    module = __import__(pattern_obj.__module__, fromlist=["dummy"])
    lookback = getattr(module, "LOOKBACK", None)
    if isinstance(lookback, int) and lookback > 0:
        return max(lookback + 1, 20)
    return DEFAULT_WINDOW_SIZE


def run_historical_backtest(
    symbol: str,
    pattern_obj,
    take_profit_pct: float = 0.02,
    stop_loss_pct: float = 0.01,
) -> dict:
    df = _load_symbol_history(symbol)
    window_size = _resolve_window_size(pattern_obj)

    wins = 0
    losses = 0
    total_trades = 0

    for index in range(len(df)):
        window = df.iloc[max(0, index - window_size + 1): index + 1]
        if window.empty:
            continue

        opens = window["open"].astype(float).tolist()
        highs = window["high"].astype(float).tolist()
        lows = window["low"].astype(float).tolist()
        closes = window["close"].astype(float).tolist()
        volumes = window["volume"].astype(float).tolist()

        if not pattern_obj.evaluate(opens, highs, lows, closes, volumes):
            continue

        entry_price = closes[-1]
        take_profit = entry_price * (1 + take_profit_pct)
        stop_loss = entry_price * (1 - stop_loss_pct)

        trade_outcome = None
        for future_index in range(index + 1, len(df)):
            future_row = df.iloc[future_index]
            candle_high = float(future_row["high"])
            candle_low = float(future_row["low"])

            if candle_low <= stop_loss and candle_high >= take_profit:
                trade_outcome = "loss"
                break
            if candle_low <= stop_loss:
                trade_outcome = "loss"
                break
            if candle_high >= take_profit:
                trade_outcome = "win"
                break

        if trade_outcome is None:
            continue

        total_trades += 1
        if trade_outcome == "win":
            wins += 1
        else:
            losses += 1

    win_rate = (wins / total_trades) if total_trades else 0.0
    return {
        "win_rate": float(win_rate),
        "total_trades": int(total_trades),
        "wins": int(wins),
        "losses": int(losses),
    }
