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
    stop_loss_pct = 0.01
    take_profit_pct = 0.02

    def next(self) -> None:
        if self.position:
            return

        signal = bool(self.data.Signal[-1]) if "Signal" in self.data.df.columns else False
        if not signal:
            return

        entry_price = float(self.data.Close[-1])
        self.buy(
            sl=entry_price * (1 - self.stop_loss_pct),
            tp=entry_price * (1 + self.take_profit_pct),
        )


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

    backtest = Backtest(
        df[["Open", "High", "Low", "Close", "Volume", "Signal"]],
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
