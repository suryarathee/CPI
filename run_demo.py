"""
run_demo.py
-----------
One-shot launcher for the Kinetic Monolith trading terminal.

Steps
-----
1. Pre-flight check  — downloads any missing parquet data via data.py
2. Kafka health check — warns if Docker / Kafka is not up
3. Starts stream_parquet.py silently in the background (no cmd window)
4. Launches dashboard_qt.py in this same terminal (foreground)
   The PyQt5 window appears; close it to exit.

Usage
-----
    python run_demo.py                # full run
    python run_demo.py --skip-download
    python run_demo.py --force-download
    python run_demo.py --no-stream    # GUI only (Algo Builder testing)
"""
from __future__ import annotations

import argparse
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PARQUET_DIR = ROOT / "parquet_data"
KAFKA_HOST, KAFKA_PORT = "localhost", 9092

# Windows flag: spawn process with no visible console window
CREATE_NO_WINDOW = 0x08000000


# ─────────────────────────────────────────────────────────────────────────────
# 1. Data pre-flight
# ─────────────────────────────────────────────────────────────────────────────

def _count_parquet() -> int:
    if not PARQUET_DIR.exists():
        return 0
    return sum(1 for p in PARQUET_DIR.glob("*.parquet") if p.is_file())


def _ensure_data(force: bool = False) -> None:
    from data import NSE_STOCKS, download_all, download_missing

    count = _count_parquet()
    total = len(NSE_STOCKS)

    if force:
        print(f"\n🔄  Force-download requested — refreshing all {total} symbols.\n")
        download_all()
        return

    if count == 0:
        print(f"\n📂  parquet_data/ is empty. Downloading all {total} NSE symbols…")
        print("    This may take 3–5 minutes on first run.\n")
        download_all()
    elif count < total:
        missing = total - count
        print(f"\n📂  Found {count}/{total} symbol files. Downloading {missing} missing symbol(s)…\n")
        download_missing()
    else:
        print(f"✅  Data check passed — {count}/{total} symbol files present.")


# ─────────────────────────────────────────────────────────────────────────────
# 2. Kafka health check
# ─────────────────────────────────────────────────────────────────────────────

def _check_kafka() -> bool:
    try:
        with socket.create_connection((KAFKA_HOST, KAFKA_PORT), timeout=2):
            return True
    except OSError:
        return False


def _kafka_status() -> None:
    if _check_kafka():
        print(f"✅  Kafka is reachable at {KAFKA_HOST}:{KAFKA_PORT}")
    else:
        print(
            f"\n⚠️   Kafka is NOT reachable at {KAFKA_HOST}:{KAFKA_PORT}\n"
            f"    Live scanner requires Kafka — start it with:\n"
            f"        docker compose up -d\n"
            f"\n"
            f"    ► The 🧬 Algo Builder tab works WITHOUT Kafka.\n"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Silent background launcher (no cmd window)
# ─────────────────────────────────────────────────────────────────────────────

def _launch_background(script: str) -> subprocess.Popen:
    """
    Starts `python <script>` as a detached background process with no
    console window. stderr/stdout are piped so they don't pollute this terminal.
    """
    return subprocess.Popen(
        [sys.executable, str(ROOT / script)],
        cwd=str(ROOT),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=CREATE_NO_WINDOW,  # Windows: no visible cmd window
    )


def _launch_foreground(script: str) -> int:
    """
    Runs `python <script>` in this terminal, blocking until the process exits.
    Returns the exit code.
    """
    proc = subprocess.Popen(
        [sys.executable, str(ROOT / script)],
        cwd=str(ROOT),
    )
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
    return proc.returncode


# ─────────────────────────────────────────────────────────────────────────────
# 4. Main
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Launch the Kinetic Monolith NSE trading terminal."
    )
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skip the data pre-flight check.",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download ALL symbols even if already present.",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Only launch the dashboard (no stream_parquet). Good for Algo Builder testing.",
    )
    args = parser.parse_args()

    print("=" * 58)
    print("  🏦  KINETIC MONOLITH — NSE Algorithmic Trading Terminal")
    print("=" * 58)

    # ── Step 1: Ensure data ───────────────────────────────────────
    if args.skip_download:
        count = _count_parquet()
        if count == 0:
            print(
                "\n❌  --skip-download set but parquet_data/ is empty.\n"
                "    Run without the flag to download data first.\n"
            )
            sys.exit(1)
        print(f"⏭️   Skipping download — {count} parquet file(s) present.")
    else:
        _ensure_data(force=args.force_download)

    print()

    # ── Step 2: Kafka status ──────────────────────────────────────
    kafka_ok = _check_kafka()
    _kafka_status()
    print()

    # ── Step 3: Background stream (silent, no window) ─────────────
    stream_proc: subprocess.Popen | None = None
    if not args.no_stream and kafka_ok:
        print("🔁  Starting Market Replay Engine in background…")
        stream_proc = _launch_background("stream_parquet.py")
        time.sleep(1)   # brief pause so stream initialises before GUI connects
        print("    Done.\n")
    elif not args.no_stream and not kafka_ok:
        print("⏭️   Skipping stream engine (Kafka not available).\n")

    # ── Step 4: Dashboard (foreground — blocks until window closed) ─
    print("🖥️   Launching dashboard… (close the window to exit)\n")
    if kafka_ok and not args.no_stream:
        print("  NEXT STEPS INSIDE THE APP:")
        print("  1. Select symbols in the right panel")
        print("  2. Click  ► START TEST  to begin live replay")
        print("  3. Watch AI alerts appear in the Swarm Alerts feed")
        print("  4. Switch to 🧬 Algo Builder for Text-to-Algo features")
    else:
        print("  → Switch to the 🧬 Algo Builder tab to test Text-to-Algo")
    print()

    exit_code = _launch_foreground("dashboard_qt.py")

    # ── Step 5: Cleanup ───────────────────────────────────────────
    if stream_proc and stream_proc.poll() is None:
        print("\n🛑  Stopping background stream engine…")
        stream_proc.terminate()
        try:
            stream_proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            stream_proc.kill()
        print("    Stream engine stopped.")

    print("\n✅  Kinetic Monolith exited cleanly.")
    sys.exit(exit_code)


if __name__ == "__main__":
    if os.name != "nt":
        print("[ERROR] run_demo.py uses Windows process flags (CREATE_NO_WINDOW).")
        print("        On Linux/Mac, run scripts manually in separate terminals.")
        sys.exit(1)
    main()
