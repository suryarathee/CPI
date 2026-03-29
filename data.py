"""
data.py
-------
Downloads 5-minute OHLCV data for NSE Nifty-50 stocks from Yahoo Finance
and saves each symbol as a Parquet file in parquet_data/.

Usage
-----
# Download everything from scratch:
    python data.py

# Download only symbols that are currently missing:
    python data.py --missing-only

Programmatic API (used by run_demo.py):
    from data import download_missing, NSE_STOCKS
    download_missing()          # downloads only what isn't already on disk
    download_missing(["INFY"])  # download a specific list
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

# ── NSE Nifty-50 symbol list (Yahoo Finance format) ───────────────────────────
NSE_STOCKS: list[str] = [
    "RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "INFY.NS", "ICICIBANK.NS",
    "SBIN.NS", "BHARTIARTL.NS", "ITC.NS", "LT.NS", "BAJFINANCE.NS",
    "KOTAKBANK.NS", "AXISBANK.NS", "INDUSINDBK.NS", "BAJAJFINSV.NS",
    "SBILIFE.NS", "HDFCLIFE.NS", "SHRIRAMFIN.NS",
    "HCLTECH.NS", "WIPRO.NS", "TECHM.NS", "LTIM.NS",
    "HINDUNILVR.NS", "ASIANPAINT.NS", "NESTLEIND.NS", "BRITANNIA.NS",
    "TATACONSUM.NS", "TITAN.NS",
    "M&M.NS", "MARUTI.NS", "BAJAJ-AUTO.NS",
    "EICHERMOT.NS", "HEROMOTOCO.NS",
    "SUNPHARMA.NS", "DRREDDY.NS", "DIVISLAB.NS", "CIPLA.NS", "APOLLOHOSP.NS",
    "TATASTEEL.NS", "JSWSTEEL.NS", "HINDALCO.NS", "COALINDIA.NS",
    "ONGC.NS", "NTPC.NS", "POWERGRID.NS", "BPCL.NS",
    "ULTRACEMCO.NS", "GRASIM.NS", "ADANIENT.NS", "ADANIPORTS.NS",
]

PARQUET_DIR = Path("parquet_data")
# yfinance allows max 60 days of 5m data
DATA_DAYS = 59


def _clean_name(yf_symbol: str) -> str:
    """'RELIANCE.NS' → 'RELIANCE'"""
    return yf_symbol.replace(".NS", "")


def _parquet_path(clean_symbol: str) -> Path:
    return PARQUET_DIR / f"{clean_symbol}.parquet"


def _missing_symbols(symbols: list[str] | None = None) -> list[str]:
    """Returns the subset of symbols whose parquet file does not exist yet."""
    to_check = symbols or NSE_STOCKS
    return [s for s in to_check if not _parquet_path(_clean_name(s)).exists()]


def _fetch_one(yf_symbol: str, start: datetime, end: datetime) -> bool:
    """Downloads and saves one symbol. Returns True on success."""
    clean = _clean_name(yf_symbol)
    out_path = _parquet_path(clean)

    try:
        df = yf.download(
            yf_symbol,
            start=start,
            end=end,
            interval="5m",
            progress=False,
            auto_adjust=True,
        )

        if df.empty:
            print(f"  ⚠  No data returned for {clean} (market may be closed or symbol delisted).")
            return False

        # Flatten MultiIndex columns that yfinance sometimes emits
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df = df.reset_index()

        # Normalise column names — yfinance may call the time col 'Datetime' or 'Date'
        time_col = next((c for c in df.columns if c.lower() in ("datetime", "date")), None)
        if time_col is None:
            print(f"  ⚠  Could not find timestamp column for {clean}. Columns: {list(df.columns)}")
            return False

        df = df.rename(columns={time_col: "timestamp"})
        rename = {"Open": "open", "High": "high", "Low": "low",
                  "Close": "close", "Volume": "volume"}
        df = df.rename(columns=rename)

        required = ["timestamp", "open", "high", "low", "close", "volume"]
        missing_cols = [c for c in required if c not in df.columns]
        if missing_cols:
            print(f"  ⚠  {clean} is missing columns: {missing_cols}")
            return False

        df = df[required].copy()
        df["symbol"] = clean

        # Ensure timestamp is timezone-aware UTC
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
        df = df.dropna(subset=["timestamp", "close"])

        PARQUET_DIR.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_path, engine="pyarrow", index=False)
        print(f"  ✅ {clean:<16} — {len(df):>5} candles saved → {out_path}")
        return True

    except Exception as exc:
        print(f"  ❌ {clean:<16} — Error: {exc}")
        return False


def download_missing(symbols: list[str] | None = None) -> list[str]:
    """
    Downloads parquet data only for symbols that don't already have a file.

    Parameters
    ----------
    symbols : list of Yahoo Finance ticker strings, e.g. ['RELIANCE.NS']
              Pass None to check the full NSE_STOCKS list.

    Returns
    -------
    list[str]
        Clean symbol names (e.g. 'RELIANCE') that were successfully downloaded.
    """
    missing = _missing_symbols(symbols)
    if not missing:
        print("✅ All symbol data is already present — nothing to download.")
        return []

    print(f"\n📥  Downloading data for {len(missing)} missing symbol(s)…")
    print(f"    Period: last {DATA_DAYS} days of 5-minute candles\n")

    end = datetime.now()
    start = end - timedelta(days=DATA_DAYS)

    succeeded: list[str] = []
    for i, yf_symbol in enumerate(missing, 1):
        clean = _clean_name(yf_symbol)
        print(f"[{i:>2}/{len(missing)}] Fetching {clean}…")
        if _fetch_one(yf_symbol, start, end):
            succeeded.append(clean)

    print(f"\n{'─'*50}")
    print(f"Download complete: {len(succeeded)}/{len(missing)} succeeded.")
    if len(succeeded) < len(missing):
        failed = [_clean_name(s) for s in missing if _clean_name(s) not in succeeded]
        print(f"Failed symbols: {', '.join(failed)}")
    print(f"{'─'*50}\n")

    return succeeded


def download_all() -> list[str]:
    """Force re-download of every symbol (overwrites existing files)."""
    print(f"\n🔄  Force-downloading ALL {len(NSE_STOCKS)} symbols…")
    end = datetime.now()
    start = end - timedelta(days=DATA_DAYS)
    succeeded = []
    for i, yf_symbol in enumerate(NSE_STOCKS, 1):
        clean = _clean_name(yf_symbol)
        print(f"[{i:>2}/{len(NSE_STOCKS)}] Fetching {clean}…")
        if _fetch_one(yf_symbol, start, end):
            succeeded.append(clean)
    return succeeded


# ── CLI entry point ────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Download NSE 5-minute OHLCV data from Yahoo Finance."
    )
    parser.add_argument(
        "--missing-only",
        action="store_true",
        help="Only download symbols whose parquet file is not yet on disk (default: download all).",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        metavar="SYM",
        help="Space-separated list of clean symbol names to download, e.g. RELIANCE TCS INFY",
    )
    args = parser.parse_args()

    if args.symbols:
        yf_symbols = [s.upper().rstrip(".NS") + ".NS" for s in args.symbols]
        if args.missing_only:
            download_missing(yf_symbols)
        else:
            end = datetime.now()
            start = end - timedelta(days=DATA_DAYS)
            for sym in yf_symbols:
                print(f"Fetching {_clean_name(sym)}…")
                _fetch_one(sym, start, end)
    elif args.missing_only:
        download_missing()
    else:
        download_all()


if __name__ == "__main__":
    main()