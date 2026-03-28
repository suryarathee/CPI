import os
import subprocess
import time


START_SIGNAL_FILE = ".stream_start.signal"


def main():
    print("==================================================")
    print("INITIATING PATTERN INTELLIGENCE SUITE")
    print("==================================================")

    if os.name != "nt":
        print("[ERROR] This launcher is designed for Windows.")
        return

    if os.path.exists(START_SIGNAL_FILE):
        os.remove(START_SIGNAL_FILE)

    print("[1/3] Booting up AI Analyzer Swarm...")
    subprocess.Popen(["start", "cmd", "/k", "python", "analyzer.py"], shell=True)

    time.sleep(2)

    print("[2/3] Launching PyQt5 Desktop Terminal...")
    subprocess.Popen(["start", "cmd", "/k", "python", "dashboard_qt.py"], shell=True)

    time.sleep(2)

    print("[3/3] Priming the Market Replay Engine...")
    subprocess.Popen(["start", "cmd", "/k", "python", "stream_parquet.py"], shell=True)

    print("\nAll systems online!")
    print("Arrange your windows, then click START TEST RUN in the PyQt window to begin the simulation.")


if __name__ == "__main__":
    main()
