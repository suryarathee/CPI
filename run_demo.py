import subprocess
import time
import os


def main():
    print("==================================================")
    print("🚀 INITIATING PATTERN INTELLIGENCE SUITE")
    print("==================================================")

    # Ensure we are on Windows for the 'start cmd' command to work
    if os.name != 'nt':
        print("[ERROR] This launcher is designed for Windows.")
        return

    # 1. Launch the AI Brain
    print("[1/3] Booting up AI Analyzer Swarm...")
    # /k keeps the window open even if the script crashes, great for debugging
    subprocess.Popen(['start', 'cmd', '/k', 'python', 'analyzer.py'], shell=True)

    # Wait 2 seconds so the AI has time to load the 50-day Parquet file into memory
    time.sleep(2)

    # 2. Launch the Desktop Terminal
    print("[2/3] Launching PyQt5 Desktop Terminal...")
    subprocess.Popen(['start', 'cmd', '/k', 'python', 'dashboard_qt.py'], shell=True)

    # Wait 2 seconds for the GUI engine to render
    time.sleep(2)

    # 3. Launch the Data Streamer
    print("[3/3] Priming the Market Replay Engine...")
    subprocess.Popen(['start', 'cmd', '/k', 'python', 'stream_parquet.py'], shell=True)

    print("\n✅ All systems online!")
    print("👉 Arrange your windows, then press ENTER in the Streamer terminal to begin the simulation.")


if __name__ == "__main__":
    main()