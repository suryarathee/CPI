from backtester import run_historical_backtest
from patterns import BullishEngulfing, VolumeBreakout
import json

symbol = "RELIANCE"
patterns = [BullishEngulfing(), VolumeBreakout()]

for p in patterns:
    stats = run_historical_backtest(symbol, p)
    print(f"--- {p.name} ---")
    print(json.dumps(stats, indent=2))
