import yfinance as yf
import pandas as pd
import os
from datetime import datetime, timedelta
NSE_STOCKS = [
    'RELIANCE.NS', 'TCS.NS', 'HDFCBANK.NS', 'INFY.NS', 'ICICIBANK.NS',
    'SBIN.NS', 'BHARTIARTL.NS', 'ITC.NS', 'LT.NS', 'BAJFINANCE.NS',
    'KOTAKBANK.NS', 'AXISBANK.NS', 'INDUSINDBK.NS', 'BAJAJFINSV.NS',
    'SBILIFE.NS', 'HDFCLIFE.NS', 'SHRIRAMFIN.NS',
    'HCLTECH.NS', 'WIPRO.NS', 'TECHM.NS', 'LTIM.NS',
    'HINDUNILVR.NS', 'ASIANPAINT.NS', 'NESTLEIND.NS', 'BRITANNIA.NS',
    'TATACONSUM.NS', 'TITAN.NS',
    'M&M.NS', 'MARUTI.NS', 'TATAMOTORS.NS', 'BAJAJ-AUTO.NS',
    'EICHERMOT.NS', 'HEROMOTOCO.NS',
    'SUNPHARMA.NS', 'DRREDDY.NS', 'DIVISLAB.NS', 'CIPLA.NS', 'APOLLOHOSP.NS',
    'TATASTEEL.NS', 'JSWSTEEL.NS', 'HINDALCO.NS', 'COALINDIA.NS',
    'ONGC.NS', 'NTPC.NS', 'POWERGRID.NS', 'BPCL.NS',
    'ULTRACEMCO.NS', 'GRASIM.NS', 'ADANIENT.NS', 'ADANIPORTS.NS'
]


def main():

    os.makedirs("parquet_data", exist_ok=True)
    end_date = datetime.now()
    start_date = end_date - timedelta(days=59)

    for symbol in NSE_STOCKS:
        clean_symbol = symbol.replace('.NS', '')
        print(f"[FETCHING] Downloading 60 days of 5m data for {clean_symbol}...")

        try:
            # Download the data
            df = yf.download(symbol, start=start_date, end=end_date, interval="5m", progress=False)

            if df.empty:
                print(f"[WARNING] No data found for {clean_symbol}.")
                continue
            df.reset_index(inplace=True)
            df = df[['Datetime', 'Close', 'Volume']].copy()
            df.columns = ['timestamp', 'price', 'volume']
            df['symbol'] = clean_symbol
            file_path = f"parquet_data/{clean_symbol}.parquet"
            df.to_parquet(file_path, engine='pyarrow')

            print(f"[SUCCESS] Saved {len(df)} candles to {file_path}")

        except Exception as e:
            print(f"[ERROR] Failed to process {symbol}: {e}")

    print("\nAll data successfully downloaded and compressed to Parquet!")


if __name__ == "__main__":
    main()