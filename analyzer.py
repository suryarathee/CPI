import json
import pandas as pd
from confluent_kafka import Consumer, KafkaError
from collections import deque

# --- CONFIGURATION ---
KAFKA_BROKER = 'localhost:9092'
TOPIC = 'nse_raw_ticks'
WINDOW_SIZE = 50 

def main():
    # 1. Initialize the Kafka Consumer
    conf = {
        'bootstrap.servers': KAFKA_BROKER,
        'group.id': 'pattern-intelligence-engine',
        'auto.offset.reset': 'latest' # Only read new ticks, don't read old history
    }
    
    consumer = Consumer(conf)
    consumer.subscribe([TOPIC])
    
    print("[PYTHON] Pattern Intelligence Engine Started.")
    print("[PYTHON] Waiting for data from C++ Ingester...\n")

    # High-speed memory buffer (automatically drops old ticks to prevent memory leaks)
    tick_buffer = deque(maxlen=WINDOW_SIZE)

    try:
        while True:
            # 2. Pull data from Kafka
            msg = consumer.poll(timeout=1.0)
            
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() != KafkaError._PARTITION_EOF:
                    print(f"[ERROR] {msg.error()}")
                continue

            # 3. Parse the data
            try:
                raw_data = json.loads(msg.value().decode('utf-8'))
                
                # Binance sends price/volume as strings, we must convert to floats
                tick = {
                    'symbol': raw_data['symbol'],
                    'price': float(raw_data['price']),
                    'volume': float(raw_data['volume'])
                }
                tick_buffer.append(tick)
                
                # Print a subtle heartbeat so we know data is flowing
                print(f"Tick received: {tick['symbol']} @ ${tick['price']:.2f}", end='\r')

            except Exception as e:
                print(f"\n[ERROR] Parsing failed: {e}")
                continue

            # 4. CHART PATTERN INTELLIGENCE LOGIC
            # Only run the math if our buffer is completely full
            if len(tick_buffer) == WINDOW_SIZE:
                
                # Convert the buffer to a Pandas DataFrame for instant vector math
                df = pd.DataFrame(tick_buffer)
                
                current_price = df.iloc[-1]['price']
                current_vol = df.iloc[-1]['volume']
                
                # Math: Calculate the baseline (average of the previous 49 ticks)
                avg_price = df.iloc[:-1]['price'].mean()
                avg_vol = df.iloc[:-1]['volume'].mean()
                max_price = df.iloc[:-1]['price'].max()

                # Pattern Trigger: "Bullish Volume Breakout"
                # If current price breaks the recent high AND volume is 3x the average
                if current_price > max_price and current_vol > (avg_vol * 3):
                    
                    print("\n" + "="*60)
                    print(f"🚨 PATTERN DETECTED: Bullish Micro-Breakout on {tick['symbol']}")
                    print("="*60)
                    print(f"Historical Edge   : 68% Win Rate (Simulated)")
                    print(f"Average Move      : +1.2% (Simulated)")
                    print(f"Plain English     : {tick['symbol']} just broke above its recent resistance level of ${max_price:.2f}.")
                    print(f"                    This move is backed by a massive volume spike ({current_vol:.2f} BTC), ")
                    print(f"                    indicating aggressive institutional buying.")
                    print("="*60 + "\n")
                    
                    # Clear the buffer slightly so we don't trigger the same breakout twice in a row
                    tick_buffer.clear()

    except KeyboardInterrupt:
        print("\n[PYTHON] Engine gracefully shutting down.")
    finally:
        consumer.close()

if __name__ == "__main__":
    main()