"""
backtester.py
-------------
A backtesting bridge that uses the `backtesting` library to evaluate 
patterns on historical data. 
"""

import pandas as pd
import numpy as np
from pathlib import Path
from backtesting import Backtest, Strategy
from patterns.base import BasePattern

# Use the same parquet directory as other modules
PARQUET_DIR = Path(__file__).resolve().parent / "parquet_data"

def _load_history(symbol: str) -> pd.DataFrame:
    """
    Loads and standardizes historical parquet data for the backtesting library.
    """
    parquet_path = PARQUET_DIR / f"{symbol}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"Historical parquet not found for {symbol}")

    df = pd.read_parquet(parquet_path)
    
    # Standardize columns for backtesting.py (requires Open, High, Low, Close, Volume)
    rename_map = {
        "timestamp": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume"
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
    
    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], utc=True)
        df = df.set_index("Date").sort_index()
    
    # Ensure all required columns are present and numeric
    required_cols = ["Open", "High", "Low", "Close", "Volume"]
    for c in required_cols:
        if c not in df.columns:
            # If missing volume, default to 0
            if c == "Volume":
                df[c] = 0.0
            else:
                raise ValueError(f"Missing required column: {c}")
        df[c] = pd.to_numeric(df[c], errors="coerce")
        
    return df.dropna(subset=required_cols).copy()

class PatternStrategy(Strategy):
    """
    Executes a simple SL/TP trade when a pattern signal is detected.
    Includes a time-based exit after 100 bars.
    """
    # Parameters for the strategy
    stop_loss_pct = 0.015  # 1.5% SL
    take_profit_pct = 0.03  # 3% TP
    exit_bars = 50         # Exit after 50 bars (approx half trading day)

    def init(self):
        # Pre-calculated Signal column from the data
        self.signal = self.data.Signal
        self.entry_idx = 0

    def next(self):
        # 1. Check for time-based exit
        if self.position:
            if len(self.data) - self.entry_idx >= self.exit_bars:
                self.position.close()
            return

        # 2. Only enter if not already in a position and a signal is triggered
        if self.data.Signal[-1] > 0:
            price = self.data.Close[-1]
            sl = price * (1 - self.stop_loss_pct)
            tp = price * (1 + self.take_profit_pct)
            self.buy(sl=sl, tp=tp)
            self.entry_idx = len(self.data)

def run_historical_backtest(symbol: str, pattern: BasePattern) -> dict:
    """
    Runs a historical backtest for a specific symbol and pattern.
    """
    try:
        df = _load_history(symbol)
        if len(df) < 100: # Minimum data check
            return _empty_stats()
            
        # 1. Optimized signal generation
        # We only need enough lookback for the pattern (max 50-100 bars)
        opens = df['Open'].tolist()
        highs = df['High'].tolist()
        lows = df['Low'].tolist()
        closes = df['Close'].tolist()
        volumes = df['Volume'].tolist()
        
        signals = [0.0] * len(df)
        lookback_limit = 100 # Maximum bars needed for current patterns
        
        for i in range(50, len(df)):
            # Slice the lists to only include the necessary lookback
            # to speed up the loop and stay within pattern memory limits
            start_idx = max(0, i - lookback_limit + 1)
            res = pattern.evaluate(
                opens[start_idx:i+1],
                highs[start_idx:i+1],
                lows[start_idx:i+1],
                closes[start_idx:i+1],
                volumes[start_idx:i+1]
            )
            signals[i] = 1.0 if res else 0.0
            
        df['Signal'] = signals
        
        # 2. Execute Backtest
        bt = Backtest(
            df, 
            PatternStrategy, 
            cash=100_000, 
            commission=0.0005, # Reduced to 0.05% for more realistic testing
            trade_on_close=True,
            exclusive_orders=True,
            finalize_trades=True
        )
        stats = bt.run()
        
        # 3. Extract metrics
        trades = stats['_trades']
        wins_df = trades[trades['PnL'] > 0]
        losses_df = trades[trades['PnL'] <= 0]
        
        # Calculate streaks
        pnl_array = trades['PnL'].values
        max_win_streak = 0
        max_loss_streak = 0
        current_win = 0
        current_loss = 0
        
        for pnl in pnl_array:
            if pnl > 0:
                current_win += 1
                current_loss = 0
                max_win_streak = max(max_win_streak, current_win)
            else:
                current_loss += 1
                current_win = 0
                max_loss_streak = max(max_loss_streak, current_loss)

        # Robust avg bars calculation
        avg_bars = 0.0
        if not trades.empty:
            # trades['Duration'] is a Timedelta or integer series
            durations = trades['Duration']
            if isinstance(durations.iloc[0], pd.Timedelta):
                # 5m candles = 300s
                avg_bars = float(durations.mean().total_seconds() / 300.0)
            else:
                avg_bars = float(durations.mean())

        return {
            "total_trades": int(stats["# Trades"]),
            "win_rate": float(stats["Win Rate [%]"]) / 100.0 if not np.isnan(stats["Win Rate [%]"]) else 0.0,
            "expectancy_pct": float(stats["Expectancy [%]"]) / 100.0 if "Expectancy [%]" in stats and not np.isnan(stats["Expectancy [%]"]) else 0.0,
            "net_return_pct": float(stats["Return [%]"]) / 100.0 if not np.isnan(stats["Return [%]"]) else 0.0,
            "avg_bars_to_exit": avg_bars,
            "wins": len(wins_df),
            "losses": len(losses_df),
            "max_win_streak": max_win_streak,
            "max_loss_streak": max_loss_streak,
        }

    except Exception as exc:
        # Silently fail for individual symbol/pattern runs to keep the UI responsive
        print(f"[BACKTEST ERROR] {symbol}/{pattern.name}: {exc}")
        return _empty_stats()

def _empty_stats() -> dict:
    return {
        "total_trades": 0,
        "win_rate": 0.0,
        "expectancy_pct": 0.0,
        "net_return_pct": 0.0,
        "avg_bars_to_exit": 0.0,
        "wins": 0,
        "losses": 0,
        "max_win_streak": 0,
        "max_loss_streak": 0,
    }
