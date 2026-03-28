import os
import json
import time
import pandas as pd
from collections import deque
from confluent_kafka import Consumer
from google import genai
from google.genai import types

# ─────────────────────────────────────────────
#  PATTERN STRATEGY IMPORTS
# ─────────────────────────────────────────────
from patterns import VolumeBreakout, BullishEngulfing

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
KAFKA_BROKER = 'localhost:9092'
TOPIC = 'nse_live_candles'
TARGET_STOCK = 'RELIANCE'
ALERTS_FILE = 'alerts.json'

try:
    client = genai.Client()
except Exception as e:
    print(f"[WARNING] Gemini Client failed to initialize. Error: {e}")
    client = None


# ─────────────────────────────────────────────
#  GEMINI MULTI-AGENT SWARM
# ─────────────────────────────────────────────
def run_multi_agent_analysis(symbol, current_price, resistance, volume_spike):
    if not client:
        return {"error": "Gemini API key not configured."}

    print(f"\n[SYSTEM] Waking up AI Swarm for {symbol}...")
    model_id = 'gemini-2.5-flash'

    quant_sys = "You are a ruthless Quantitative Analyst. Evaluate the breakout data. Output a 2-sentence technical assessment."
    quant_prompt = f"Data: {symbol}, Breakout Price: {current_price}, Prior Resistance: {resistance}, Volume Spike: {volume_spike}x average."
    quant_resp = client.models.generate_content(
        model=model_id, contents=quant_prompt,
        config=types.GenerateContentConfig(system_instruction=quant_sys, temperature=0.1)
    )
    print(f"🕵️  [Quant]: {quant_resp.text.strip()}")

    risk_sys = "You are a strict Risk Manager. Based on the Quant's assessment, define a safe stop-loss and target price (1:2 R/R). 2 sentences max."
    risk_prompt = f"Price: {current_price}. Quant Analysis: {quant_resp.text}"
    risk_resp = client.models.generate_content(
        model=model_id, contents=risk_prompt,
        config=types.GenerateContentConfig(system_instruction=risk_sys, temperature=0.1)
    )
    print(f"🛡️  [Risk]:  {risk_resp.text.strip()}")

    editor_sys = "You are a Fintech UX Writer. Combine Quant and Risk outputs into one polished, 3-bullet-point alert for a retail trader. Be objective."
    editor_prompt = f"Quant: {quant_resp.text}\nRisk: {risk_resp.text}\nFormat final alert."
    editor_resp = client.models.generate_content(
        model=model_id, contents=editor_prompt,
        config=types.GenerateContentConfig(system_instruction=editor_sys, temperature=0.3)
    )

    print("\n" + "=" * 60)
    print(f"🚀 FINAL AI SYNTHESIS FOR {symbol}")
    print("=" * 60)
    print(editor_resp.text.strip())
    print("=" * 60 + "\n")

    return {
        "symbol": symbol,
        "price": current_price,
        "quant": quant_resp.text.strip(),
        "risk": risk_resp.text.strip(),
        "synthesis": editor_resp.text.strip(),
        "timestamp": time.strftime("%H:%M:%S")
    }


# ─────────────────────────────────────────────
#  MAIN OHLCV INGESTION ENGINE
# ─────────────────────────────────────────────
def main():
    print("==================================================")
    print("🧠 TRUE OHLCV PATTERN INTELLIGENCE ENGINE")
    print("==================================================")

    try:
        print(f"[SYSTEM] Hydrating memory with historical baseline...")
        df_history = pd.read_parquet('historical_base.parquet')
        df_target = df_history[df_history['symbol'] == TARGET_STOCK].copy()
        print(f"[SYSTEM] {TARGET_STOCK} baseline loaded: {len(df_target)} historical candles.\n")
    except Exception as e:
        print("[ERROR] Could not load historical_base.parquet. Run streamer first!")
        return

    # ── Hydrate rolling OHLCV deques from historical baseline ───────────────
    # maxlen=50 gives us 1 current candle + 49-candle lookback for every pattern.
    recent_opens   = deque(df_target['open'].tail(50).tolist(),   maxlen=50)
    recent_highs   = deque(df_target['high'].tail(50).tolist(),   maxlen=50)
    recent_lows    = deque(df_target['low'].tail(50).tolist(),    maxlen=50)
    recent_closes  = deque(df_target['close'].tail(50).tolist(),  maxlen=50)
    recent_volumes = deque(df_target['volume'].tail(50).tolist(), maxlen=50)

    # ── Active pattern strategies (Strategy Design Pattern) ───────────────────
    active_patterns = [VolumeBreakout(), BullishEngulfing()]

    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': 'ohlcv-intelligence-engine',
        'auto.offset.reset': 'latest'
    })
    consumer.subscribe([TOPIC])
    print("[PYTHON] Listening to Kafka stream for live OHLCV updates...\n")

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None or msg.error():
                continue

            raw_data = json.loads(msg.value().decode('utf-8'))
            if raw_data['symbol'] != TARGET_STOCK:
                continue

            # ── Extract full OHLCV tick ───────────────────────────────────────
            tick_open  = float(raw_data['open'])
            tick_high  = float(raw_data['high'])
            tick_low   = float(raw_data['low'])
            tick_close = float(raw_data['close'])
            tick_vol   = float(raw_data['volume'])

            # ── Append newest tick to rolling deques BEFORE evaluation ─────────
            # Patterns always read the full window; current candle is at index -1.
            recent_opens.append(tick_open)
            recent_highs.append(tick_high)
            recent_lows.append(tick_low)
            recent_closes.append(tick_close)
            recent_volumes.append(tick_vol)

            # ── Convert deques to lists once (cheap, avoids repeated conversion)
            l_opens   = list(recent_opens)
            l_highs   = list(recent_highs)
            l_lows    = list(recent_lows)
            l_closes  = list(recent_closes)
            l_volumes = list(recent_volumes)

            # ── Iterate over all active pattern strategies ────────────────────
            pattern_fired = False
            for pattern in active_patterns:
                if pattern.evaluate(l_opens, l_highs, l_lows, l_closes, l_volumes):
                    pattern_fired = True

                    # Pre-compute summary stats for the Gemini swarm context
                    resistance_line = max(l_highs[:-1]) if len(l_highs) > 1 else tick_high
                    avg_vol = (
                        sum(l_volumes[:-1]) / len(l_volumes[:-1])
                        if len(l_volumes) > 1 else 1.0
                    )
                    vol_multiplier = tick_vol / avg_vol if avg_vol else 0.0

                    print("\n" + "🔥" * 20)
                    print(f"🚨 PATTERN DETECTED [{pattern.name}]: {TARGET_STOCK} at ₹{tick_close:.2f}!")
                    print(f"   Resistance Level  : ₹{resistance_line:.2f}")
                    print(f"   Volume Spike      : {vol_multiplier:.1f}x Average")
                    print("🔥" * 20)

                    alert_data = run_multi_agent_analysis(
                        symbol=f"{TARGET_STOCK} [{pattern.name}]",
                        current_price=tick_close,
                        resistance=round(resistance_line, 2),
                        volume_spike=round(vol_multiplier, 1),
                    )

                    if "error" not in alert_data:
                        with open(ALERTS_FILE, "w") as f:
                            json.dump(alert_data, f)

                    # Reset rolling windows so the same candle can't re-fire
                    recent_opens.clear()
                    recent_highs.clear()
                    recent_lows.clear()
                    recent_closes.clear()
                    recent_volumes.clear()
                    break   # one alert per candle; remove if multi-pattern desired

            if not pattern_fired:
                print(
                    f"Analyzing Live OHLCV -> {raw_data['timestamp']} "
                    f"| C: ₹{tick_close:.2f} | Vol: {tick_vol}",
                    end='\r',
                )

    except KeyboardInterrupt:
        print("\n[PYTHON] Engine gracefully shutting down.")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()