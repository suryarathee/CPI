from __future__ import annotations

import json
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

import pandas as pd
from confluent_kafka import Consumer

from adk_swarm import analyze_trade_event
from backtester import run_historical_backtest
from env_loader import load_local_env
from patterns import BullishEngulfing, VolumeBreakout
from runtime_config import load_selection


load_local_env()


KAFKA_BROKER = "localhost:9092"
TOPIC = "nse_live_candles"
ALERTS_FILE = "alerts.json"
MAX_ALERT_HISTORY = 50


class SymbolRuntime:
    def __init__(self, symbol: str, history_df: pd.DataFrame | None):
        self.symbol = symbol
        df = history_df if history_df is not None else pd.DataFrame()
        self.opens = deque(df.get("open", pd.Series(dtype=float)).tail(50).tolist(), maxlen=50)
        self.highs = deque(df.get("high", pd.Series(dtype=float)).tail(50).tolist(), maxlen=50)
        self.lows = deque(df.get("low", pd.Series(dtype=float)).tail(50).tolist(), maxlen=50)
        self.closes = deque(df.get("close", pd.Series(dtype=float)).tail(50).tolist(), maxlen=50)
        self.volumes = deque(df.get("volume", pd.Series(dtype=float)).tail(50).tolist(), maxlen=50)

    def append(self, raw_data: dict) -> None:
        self.opens.append(float(raw_data["open"]))
        self.highs.append(float(raw_data["high"]))
        self.lows.append(float(raw_data["low"]))
        self.closes.append(float(raw_data["close"]))
        self.volumes.append(float(raw_data["volume"]))

    def snapshot(self) -> tuple[list, list, list, list, list]:
        return (
            list(self.opens),
            list(self.highs),
            list(self.lows),
            list(self.closes),
            list(self.volumes),
        )

    def reset_windows(self) -> None:
        self.opens.clear()
        self.highs.clear()
        self.lows.clear()
        self.closes.clear()
        self.volumes.clear()


def load_alert_history() -> list[dict]:
    try:
        with open(ALERTS_FILE, "r", encoding="utf-8") as f:
            payload = json.load(f)
    except Exception:
        return []

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        return [payload]
    return []


def store_alert(alert: dict, alert_lock: Lock) -> None:
    with alert_lock:
        alerts = load_alert_history()
        alerts.insert(0, alert)
        alerts = alerts[:MAX_ALERT_HISTORY]
        with open(ALERTS_FILE, "w", encoding="utf-8") as f:
            json.dump(alerts, f, indent=2)


def run_multi_agent_analysis(symbol, current_price, pattern_name, resistance, volume_spike, win_rate):
    print(f"\n[SYSTEM] Waking up ADK Swarm for {symbol}...")

    synthesis = analyze_trade_event(
        symbol=symbol,
        current_price=current_price,
        pattern_name=pattern_name,
        win_rate=win_rate,
    ).strip()

    quant_summary = (
        f"{pattern_name} triggered on {symbol} at {current_price:.2f} after clearing "
        f"resistance near {resistance:.2f} with {volume_spike:.1f}x relative volume. "
        f"Historical backtest win rate is {win_rate:.2%}."
    )
    risk_per_share = current_price * 0.01
    risk_summary = (
        f"Use a strict 1:2 framework with stop-loss near {current_price - risk_per_share:.2f} "
        f"and target near {current_price + (2 * risk_per_share):.2f}."
    )

    print(f"[Pattern] {quant_summary}")
    print(f"[Risk] {risk_summary}")
    print("\n" + "=" * 60)
    print(f"FINAL ADK SYNTHESIS FOR {symbol}")
    print("=" * 60)
    print(synthesis)
    print("=" * 60 + "\n")

    return {
        "symbol": symbol,
        "price": current_price,
        "pattern": pattern_name,
        "win_rate": win_rate,
        "quant": quant_summary,
        "risk": risk_summary,
        "synthesis": synthesis,
        "timestamp": time.strftime("%H:%M:%S"),
        "event_id": f"{symbol}:{pattern_name}:{time.time_ns()}",
    }


def initialize_symbol_state(df_history: pd.DataFrame, symbol: str) -> SymbolRuntime:
    symbol_history = df_history[df_history["symbol"] == symbol].copy()
    return SymbolRuntime(symbol, symbol_history)


def refresh_runtime_state(
    df_history: pd.DataFrame,
    active_patterns: list,
    symbol_states: dict[str, SymbolRuntime],
    pattern_stats: dict[tuple[str, str], dict],
) -> tuple[list[str], str]:
    selection = load_selection()
    selected_symbols = selection["selected_symbols"]
    focus_symbol = selection["focus_symbol"]

    for symbol in selected_symbols:
        if symbol not in symbol_states:
            symbol_states[symbol] = initialize_symbol_state(df_history, symbol)
        for pattern in active_patterns:
            key = (symbol, pattern.name)
            if key not in pattern_stats:
                try:
                    stats = run_historical_backtest(symbol, pattern)
                    pattern_stats[key] = stats
                    print(
                        f"[SYSTEM] {symbol} {pattern.name}: "
                        f"win_rate={stats['win_rate']:.2%} over {stats['total_trades']} trades."
                    )
                except Exception as exc:
                    print(f"[WARNING] Backtest failed for {symbol} {pattern.name}: {exc}")
                    pattern_stats[key] = {"win_rate": 0.0, "total_trades": 0, "wins": 0, "losses": 0}

    stale_symbols = [symbol for symbol in list(symbol_states) if symbol not in selected_symbols]
    for symbol in stale_symbols:
        del symbol_states[symbol]

    stale_keys = [key for key in list(pattern_stats) if key[0] not in selected_symbols]
    for key in stale_keys:
        del pattern_stats[key]

    return selected_symbols, focus_symbol


def process_detection(
    symbol: str,
    current_price: float,
    pattern_name: str,
    resistance: float,
    volume_spike: float,
    win_rate: float,
    alert_lock: Lock,
) -> None:
    alert_data = run_multi_agent_analysis(
        symbol=symbol,
        current_price=current_price,
        pattern_name=pattern_name,
        resistance=round(resistance, 2),
        volume_spike=round(volume_spike, 1),
        win_rate=win_rate,
    )
    store_alert(alert_data, alert_lock)


def main():
    print("==================================================")
    print("TRUE OHLCV PATTERN INTELLIGENCE ENGINE")
    print("==================================================")

    try:
        print("[SYSTEM] Hydrating memory with historical baseline...")
        df_history = pd.read_parquet("historical_base.parquet")
        print(f"[SYSTEM] Historical baseline loaded: {len(df_history)} candles.\n")
    except Exception:
        print("[ERROR] Could not load historical_base.parquet. Run streamer first!")
        return

    active_patterns = [VolumeBreakout(), BullishEngulfing()]
    symbol_states: dict[str, SymbolRuntime] = {}
    pattern_stats: dict[tuple[str, str], dict] = {}
    alert_lock = Lock()

    selected_symbols, _focus_symbol = refresh_runtime_state(
        df_history=df_history,
        active_patterns=active_patterns,
        symbol_states=symbol_states,
        pattern_stats=pattern_stats,
    )
    print(f"[SYSTEM] Monitoring symbols: {', '.join(selected_symbols)}")

    consumer = Consumer(
        {
            "bootstrap.servers": KAFKA_BROKER,
            "group.id": "ohlcv-intelligence-engine",
            "auto.offset.reset": "latest",
        }
    )
    consumer.subscribe([TOPIC])
    print("[PYTHON] Listening to Kafka stream for live OHLCV updates...\n")

    with ThreadPoolExecutor(max_workers=4) as executor:
        try:
            while True:
                selected_symbols, _focus_symbol = refresh_runtime_state(
                    df_history=df_history,
                    active_patterns=active_patterns,
                    symbol_states=symbol_states,
                    pattern_stats=pattern_stats,
                )

                msg = consumer.poll(timeout=1.0)
                if msg is None or msg.error():
                    continue

                raw_data = json.loads(msg.value().decode("utf-8"))
                symbol = raw_data["symbol"]
                if symbol not in symbol_states:
                    continue

                symbol_state = symbol_states[symbol]
                symbol_state.append(raw_data)
                l_opens, l_highs, l_lows, l_closes, l_volumes = symbol_state.snapshot()

                tick_high = float(raw_data["high"])
                tick_close = float(raw_data["close"])
                tick_vol = float(raw_data["volume"])

                pattern_fired = False
                for pattern in active_patterns:
                    if not pattern.evaluate(l_opens, l_highs, l_lows, l_closes, l_volumes):
                        continue

                    pattern_fired = True
                    resistance_line = max(l_highs[:-1]) if len(l_highs) > 1 else tick_high
                    avg_vol = sum(l_volumes[:-1]) / len(l_volumes[:-1]) if len(l_volumes) > 1 else 1.0
                    vol_multiplier = tick_vol / avg_vol if avg_vol else 0.0
                    win_rate = pattern_stats.get((symbol, pattern.name), {}).get("win_rate", 0.0)

                    print("\n" + ("*" * 20))
                    print(f"PATTERN DETECTED [{pattern.name}]: {symbol} at {tick_close:.2f}")
                    print(f"   Resistance Level  : {resistance_line:.2f}")
                    print(f"   Volume Spike      : {vol_multiplier:.1f}x Average")
                    print(f"   Historical WinRate: {win_rate:.2%}")
                    print("*" * 20)

                    executor.submit(
                        process_detection,
                        symbol,
                        tick_close,
                        pattern.name,
                        resistance_line,
                        vol_multiplier,
                        win_rate,
                        alert_lock,
                    )

                    symbol_state.reset_windows()
                    break

                if not pattern_fired:
                    print(
                        f"Analyzing {symbol} -> {raw_data['timestamp']} | "
                        f"C: {tick_close:.2f} | Vol: {tick_vol}",
                        end="\r",
                    )

        except KeyboardInterrupt:
            print("\n[PYTHON] Engine gracefully shutting down.")
        finally:
            consumer.close()


if __name__ == "__main__":
    main()
