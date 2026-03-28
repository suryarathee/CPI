from backtester import run_historical_backtest, _load_history
from patterns import BullishEngulfing, VolumeBreakout
import pandas as pd

symbol = "RELIANCE"
df = _load_history(symbol)
print(f"Loaded {len(df)} rows")

p = BullishEngulfing()
opens = df['Open'].tolist()
highs = df['High'].tolist()
lows = df['Low'].tolist()
closes = df['Close'].tolist()
volumes = df['Volume'].tolist()

signals = []
for i in range(len(df)):
    res = p.evaluate(opens[:i+1], highs[:i+1], lows[:i+1], closes[:i+1], volumes[:i+1])
    if res:
        signals.append(i)

print(f"BullishEngulfing signals: {len(signals)}")
if signals:
    print(f"First 5 signal indices: {signals[:5]}")

p2 = VolumeBreakout()
signals2 = []
for i in range(len(df)):
    res = p2.evaluate(opens[:i+1], highs[:i+1], lows[:i+1], closes[:i+1], volumes[:i+1])
    if res:
        signals2.append(i)

print(f"VolumeBreakout signals: {len(signals2)}")
if signals2:
    print(f"First 5 signal indices: {signals2[:5]}")
