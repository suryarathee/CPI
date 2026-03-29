from __future__ import annotations

from pathlib import Path

import pandas as pd

from scanner import pattern_signal_mask

try:
    from backtesting import Backtest, Strategy
except Exception:
    Backtest = None

    class Strategy:  # type: ignore[no-redef]
        pass


PARQUET_DIR = Path(__file__).resolve().parent / "parquet_data"


def _load_history(symbol: str) -> pd.DataFrame:
    parquet_path = PARQUET_DIR / f"{symbol}.parquet"
    if not parquet_path.exists():
        raise FileNotFoundError(f"Historical parquet not found for {symbol}: {parquet_path}")

    df = pd.read_parquet(parquet_path).copy()
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
    df = df.rename(columns={key: value for key, value in rename_map.items() if key in df.columns})
    required = ["Open", "High", "Low", "Close", "Volume"]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise ValueError(f"Historical data for {symbol} is missing: {', '.join(missing)}")

    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], errors="coerce", utc=True)
        df = df.sort_values("Date")
        df = df.dropna(subset=["Date"]).set_index("Date")

    for column in required:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    return df.dropna(subset=required).copy()


class DynamicStrategy(Strategy):
    pattern_name = "UNCONFIGURED"
    atr_multiplier_sl = 1.5
    atr_multiplier_tp = 3.0
    exit_bars = 50

    def init(self) -> None:
        self.signal = self.data.Signal
        if "ATR" in self.data.df.columns:
            self.atr = self.data.ATR
        self.entry_idx = 0
        self.highest_price = 0.0

    def next(self) -> None:
        if self.position:
            current_price = float(self.data.Close[-1])
            
            # Trailing Stop Logic
            if current_price > self.highest_price:
                self.highest_price = current_price
                if hasattr(self, 'atr'):
                    new_sl = self.highest_price - (float(self.atr[-1]) * self.atr_multiplier_sl)
                    for trade in self.trades:
                        if trade.is_long and getattr(trade, 'sl', 0) is not None:
                            if new_sl > trade.sl:
                                trade.sl = new_sl
                        
            # Time-based exit
            if len(self.data) - self.entry_idx >= self.exit_bars:
                self.position.close()
            return

        signal = bool(self.data.Signal[-1]) if "Signal" in self.data.df.columns else False
        if not signal:
            return

        entry_price = float(self.data.Close[-1])
        
        if hasattr(self, 'atr'):
            current_atr = float(self.atr[-1])
            if pd.isna(current_atr) or current_atr <= 0:
                return
            sl = entry_price - (current_atr * self.atr_multiplier_sl)
            tp = entry_price + (current_atr * self.atr_multiplier_tp)
        else:
            sl = entry_price * 0.985
            tp = entry_price * 1.03
            
        self.buy(sl=sl, tp=tp)
        self.entry_idx = len(self.data)
        self.highest_price = entry_price


def _strategy_factory(pattern_name: str) -> type[DynamicStrategy]:
    class ConfiguredDynamicStrategy(DynamicStrategy):
        pass

    ConfiguredDynamicStrategy.__name__ = f"{pattern_name}Strategy"
    ConfiguredDynamicStrategy.pattern_name = pattern_name
    return ConfiguredDynamicStrategy


def run_historical_edge(symbol: str, pattern_name: str) -> dict:
    if Backtest is None:
        raise RuntimeError("backtesting.py is not installed. Install `backtesting` to run historical edge calculations.")

    df = _load_history(symbol)
    df["Signal"] = pattern_signal_mask(df.reset_index(), pattern_name).to_numpy()

    # Calculate ATR
    high = df['High']
    low = df['Low']
    close = df['Close']
    tr1 = high - low
    tr2 = (high - close.shift(1)).abs()
    tr3 = (low - close.shift(1)).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df['ATR'] = tr.rolling(14).mean()

    backtest = Backtest(
        df[["Open", "High", "Low", "Close", "Volume", "Signal", "ATR"]],
        _strategy_factory(pattern_name),
        cash=100_000,
        commission=0.001,
        trade_on_close=True,
        exclusive_orders=True,
        finalize_trades=True,
    )
    stats = backtest.run()

    return {
        "Win Rate [%]": float(stats["Win Rate [%]"]),
        "Return [%]": float(stats["Return [%]"]),
        "Max. Drawdown [%]": float(stats["Max. Drawdown [%]"]),
        "# Trades": int(stats["# Trades"]),
    }
