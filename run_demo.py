import os
import subprocess
import time


def main():
    print("==================================================")
    print("INITIATING PATTERN INTELLIGENCE SUITE")
    print("==================================================")

    if os.name != "nt":
        print("[ERROR] This launcher is designed for Windows.")
        return

    print("[1/2] Launching PyQt5 Desktop Terminal...")
    subprocess.Popen(["start", "cmd", "/k", "python", "dashboard_qt.py"], shell=True)

    time.sleep(2)

    print("[2/2] Priming the Market Replay Engine...")
    subprocess.Popen(["start", "cmd", "/k", "python", "stream_parquet.py"], shell=True)

    print("\nAll systems online!")
    print("The dashboard now runs the scanner, edge engine, and ADK workflow in its own background thread.")


if __name__ == "__main__":
    main()
