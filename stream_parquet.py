import os
import time
import json
import pandas as pd
from confluent_kafka import Producer

# --- CONFIGURATION ---
KAFKA_BROKER = 'localhost:9092'
TOPIC = 'nse_live_candles'  # Changed topic name to reflect we are sending candles now
DATA_DIR = 'parquet_data'
STREAM_SPEED = 0.5  # Half a second per candle (Fast enough for a good demo)


def main():
    print("==================================================")
    print("🚀 INSTITUTIONAL REPLAY ENGINE: 50/10 SPLIT")
    print("==================================================")

    all_dataframes = []
    for file in os.listdir(DATA_DIR):
        if file.endswith('.parquet') and file != 'historical_base.parquet':
            df = pd.read_parquet(os.path.join(DATA_DIR, file))
            all_dataframes.append(df)

    if not all_dataframes:
        print("[ERROR] No Parquet files found!")
        return

    master_df = pd.concat(all_dataframes, ignore_index=True)
    master_df.sort_values(by='timestamp', inplace=True)

    # --- THE 50/10 SPLIT LOGIC ---
    # Find the latest date, and calculate the cutoff (10 days prior)
    max_date = master_df['timestamp'].max()
    cutoff_date = max_date - pd.Timedelta(days=10)

    historical_df = master_df[master_df['timestamp'] < cutoff_date]
    live_df = master_df[master_df['timestamp'] >= cutoff_date]

    # Save the 50 days to a single master file for the AI to load instantly
    historical_df.to_parquet('historical_base.parquet', engine='pyarrow')

    print(f"[SYSTEM] Split Complete!")
    print(f" -> Historical State (50 Days) saved: {len(historical_df)} candles.")
    print(f" -> Live Stream Queue (10 Days) ready: {len(live_df)} candles.\n")

    producer = Producer({'bootstrap.servers': KAFKA_BROKER})
    print("\n[READY] Press ENTER to begin streaming the final 10 days...")
    input()

    # --- STREAM THE LAST 10 DAYS ---
    try:
        for index, row in live_df.iterrows():
            payload = {
                "symbol": row['symbol'],
                "timestamp": str(row['timestamp']),
                "open": float(row['open']),
                "high": float(row['high']),
                "low": float(row['low']),
                "close": float(row['close']),
                "volume": float(row['volume'])
            }

            producer.produce(TOPIC, json.dumps(payload))
            producer.poll(0)

            print(f"Streamed -> {payload['symbol']} | {payload['timestamp']} | ₹{payload['close']:.2f}")
            time.sleep(STREAM_SPEED)

    except KeyboardInterrupt:
        print("\n[SYSTEM] Stream halted.")

    producer.flush()
    print("\n[SYSTEM] Stream complete.")


if __name__ == "__main__":
    main()