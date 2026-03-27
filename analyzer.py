import json
import pandas as pd
from confluent_kafka import Consumer

# import google.genai as genai # (Keep your Gemini Multi-Agent code from earlier!)

KAFKA_BROKER = 'localhost:9092'
TOPIC = 'nse_live_candles'
TARGET_SYMBOL = 'RELIANCE'  # Focus the demo on one stock for clarity


def main():
    print("==================================================")
    print("🧠 AI MULTI-AGENT SWARM INITIALIZING...")
    print("==================================================")

    # 1. HYDRATION: Load the 50 days of historical data
    try:
        print(f"[SYSTEM] Hydrating memory with 50 days of historical data...")
        df_history = pd.read_parquet('historical_base.parquet')

        # Filter for the specific stock we are demoing
        df_target = df_history[df_history['symbol'] == TARGET_SYMBOL].copy()
        print(f"[SYSTEM] {TARGET_SYMBOL} baseline loaded: {len(df_target)} historical candles.\n")
    except Exception as e:
        print("[ERROR] Could not load historical_base.parquet. Run the streamer script first!")
        return

    # 2. CONNECT TO LIVE STREAM: Listen for the final 10 days
    consumer = Consumer({
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': 'pattern-intelligence-engine',
        'auto.offset.reset': 'latest'
    })
    consumer.subscribe([TOPIC])
    print("[PYTHON] Listening to Kafka stream for live updates...\n")

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None or msg.error():
                continue

            raw_data = json.loads(msg.value().decode('utf-8'))

            # Only process the target stock for this demo
            if raw_data['symbol'] != TARGET_SYMBOL:
                continue

            tick_price = float(raw_data['price'])
            tick_vol = float(raw_data['volume'])

            # 3. UPDATE MEMORY: Append the new live candle
            new_row = pd.DataFrame([{
                'timestamp': raw_data['timestamp'],
                'symbol': raw_data['symbol'],
                'price': tick_price,
                'volume': tick_vol
            }])
            df_target = pd.concat([df_target, new_row], ignore_index=True)

            # Calculate a dynamic baseline (e.g., the 50-period max price)
            recent_high = df_target['price'].iloc[-51:-1].max()
            avg_vol = df_target['volume'].iloc[-51:-1].mean()

            # 4. PATTERN SCANNER TRIGGER
            # If the live candle breaks the 50-period high AND volume is 2x average
            if tick_price > recent_high and tick_vol > (avg_vol * 2):
                print("\n" + "🔥" * 20)
                print(f"🚨 BREAKOUT DETECTED: {TARGET_SYMBOL} at ₹{tick_price:.2f}!")
                print(f"   Historical Resistance Broken: ₹{recent_high:.2f}")
                print(f"   Volume Spike: {tick_vol} (Avg: {avg_vol:.0f})")
                print("🔥" * 20)

                # --- TRIGGER YOUR GEMINI MULTI-AGENT SWARM HERE ---
                # run_multi_agent_analysis(TARGET_SYMBOL, "Breakout", tick_price, 68.5, tick_vol/avg_vol)

            else:
                print(f"Analyzing Live Candle -> {raw_data['timestamp']} | ₹{tick_price:.2f}", end='\r')

    except KeyboardInterrupt:
        print("\n[PYTHON] Engine gracefully shutting down.")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()