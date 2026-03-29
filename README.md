# 🏦 Kinetic Monolith — NSE Algorithmic Trading Terminal

> A **real-time, AI-powered NSE stock scanner** with live candlestick charts, agentic trade alerts (Google Gemini), historical backtesting, and a **Text-to-Algo engine** that compiles plain-English trading strategies into runnable Python code — all inside a PyQt5 desktop terminal.

---

## 📋 Table of Contents

- [Architecture Overview](#-architecture-overview)
- [Prerequisites](#-prerequisites)
- [Installation](#-installation)
- [Configuration](#-configuration)
- [Running the System](#-running-the-system)
- [Feature Guide](#-feature-guide)
  - [Live Dashboard](#1-live-dashboard)
  - [Backtesting Tab](#2-backtesting-tab)
  - [Algo Builder Tab](#3-algo-builder-tab)
- [Testing Without Kafka](#-testing-without-live-kafka)
- [Project Structure](#-project-structure)
- [Troubleshooting](#-troubleshooting)

---

## 🏗️ Architecture Overview

```
parquet_data/          ← Historical OHLCV data (one .parquet per NSE symbol)
      │
      ▼
stream_parquet.py      ← Splits data 50/10 days; streams "live" candles via Kafka
      │
      ▼ (Kafka topic: nse_live_candles)
dashboard_qt.py        ← PyQt5 GUI — chart + scanner + AI alerts + Algo Builder
      │
      ├── EngineWorker  ── Pattern detection → Historical backtest → ADK AI Swarm
      │                                                       │
      │                                              adk_swarm/coordinator.py
      │                                              └── sub_agents/
      │                                                  ├── trade_analyst.py
      │                                                  ├── pattern_analyst/
      │                                                  ├── risk_analyst/
      │                                                  └── algo_architect.py  ← NEW
      │
      └── AlgoBuilderWorker  ── generate_algo_code() → sandbox_runner.py → Backtest result
```

---

## ✅ Prerequisites

| Requirement | Version | Notes |
|---|---|---|
| Python | 3.10+ | Must support `match` syntax |
| Docker Desktop | Latest | For Kafka broker |
| Google API Key | — | Gemini models via `google.adk` |
| PyQt5 | 5.15+ | pip install |
| `backtesting` | Latest | pip install |

> **Windows only** — all commands below are for PowerShell.

---

## 📦 Installation

### 1. Clone / open the project

```powershell
cd C:\Users\rathe\Desktop\ET\CPI
```

### 2. Create & activate a virtual environment (recommended)

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
```

### 3. Install Python dependencies

The full pinned list is in `requirements.txt`. Install it all at once:

```powershell
pip install -r requirements.txt
```

Or install just the critical packages manually:

```powershell
pip install PyQt5 PyQtWebEngine confluent-kafka pandas pyarrow backtesting `
            google-adk google-genai python-dotenv lightweight-charts
```

> **`backtesting` is required separately** — it is not in the main `requirements.txt`:
> ```powershell
> pip install backtesting
> ```

---

## ⚙️ Configuration

### 1. Set your Google API Key

Create (or edit) `.env` in the project root:

```env
GOOGLE_API_KEY=your_google_api_key_here
```

Get your key at → [https://aistudio.google.com/apikey](https://aistudio.google.com/apikey)

> ### 2. Verify parquet data is present

> **You don't need to do this manually.** `run_demo.py` automatically downloads
> any missing symbol data on startup. This section is just for reference.

---

## ▶️ Running the System

Everything runs from **two commands** in a single terminal:

### Step 1 — Start Kafka (Docker)

```powershell
docker compose up -d
```

That's it. Kafka starts in the background. Check it anytime at **http://localhost:8080**.

### Step 2 — Launch the terminal

```powershell
python run_demo.py
```

`run_demo.py` then handles **everything else automatically** — no extra terminals, no cmd windows:

| What it does | Detail |
|---|---|
| **Auto-downloads data** | Scans `parquet_data/` and fetches missing symbols from Yahoo Finance |
| **Checks Kafka** | Warns if Docker isn't up (but doesn't block — Algo Builder still works) |
| **Starts stream engine** | Runs `stream_parquet.py` silently in the background (no window) |
| **Launches dashboard** | Opens the PyQt5 GUI in this same terminal |
| **Auto cleanup** | When you close the GUI window, the background stream is stopped automatically |

**Optional flags:**

```powershell
# Skip data download — use existing parquet files as-is
python run_demo.py --skip-download

# Force re-download ALL symbols (refresh stale data)
python run_demo.py --force-download

# GUI only — no stream (fastest way to test the Algo Builder tab)
python run_demo.py --no-stream
```

---


## 🖥️ Feature Guide

### 1. Live Dashboard

**What it does:** Displays a real-time candlestick chart (TradingView-style) with EMA 9/21 overlays, and scans selected NSE symbols for pattern signals using AI.

**Steps:**
1. In the **right panel**, select one or more symbols from the list (multi-select with Ctrl+Click)
2. Set the **Focus** symbol — this controls which symbol's chart is displayed
3. Choose an **Agent Type** (`Coordinator`, `Trade Analyst`, `Pattern Analyst`, `Risk Analyst`)
4. Choose a **LLM Model** (default: `gemini-2.0-flash-lite-preview-02-05`)
5. Click **APPLY SAMPLE** to confirm selections
6. Click **START TEST** in the top header bar

The stream will begin replaying the last 10 days of data. When a pattern fires:

| Step | What happens |
|---|---|
| Pattern detected | `VolumeBreakout` or `BullishEngulfing` fires on the candle window |
| Backtest gate | Historical edge is checked — skipped if expectancy < 0 AND win rate < 40% |
| AI Swarm | Gemini generates a 3-bullet trade alert |
| Alert card | Card appears in the **Swarm Alerts** section |

**Clicking an alert card** opens the **Agent Deep-Dive chat panel** on the right — you can ask follow-up questions and the AI remembers the context for that alert session.

---

### 2. Backtesting Tab

**What it does:** Runs the same `VolumeBreakout` and `BullishEngulfing` patterns against the full historical parquet data for all selected symbols, and shows a metrics table.

**Steps:**
1. Select symbols in the Live Dashboard right panel first
2. Click the **Backtesting** tab
3. Click **REFRESH BACKTEST**

**Metrics displayed:**
| Column | Description |
|---|---|
| Win Rate | % of trades that hit take-profit |
| Expectancy | Average return per trade (% of capital) |
| Net Return | Total return over the historical period |
| Avg Bars | Average candles held before exit |
| Streak | Longest win streak / loss streak |

Click **CLEAR DATA** to reset the table.

---

### 3. Algo Builder Tab

**What it does:** The **Text-to-Algo Engine** — type a trading strategy in plain English, the AI generates a `backtesting.Strategy` Python class, and the system backtests it against real historical data instantly.

**Steps:**

1. Click the **🧬 Algo Builder** tab
2. Select a **Symbol** to test against (default: RELIANCE)
3. Select a **Model** from the dropdown
4. Type your strategy in the **Strategy Description** box

**Example prompts:**
```
Buy when the 9-EMA crosses above the 21-EMA and RSI(14) is above 55.
Use a 1.5× ATR stop-loss and a 3× ATR take-profit.
Exit the position when 9-EMA crosses below 21-EMA.
```

```
Buy when price breaks above the 20-period high with volume greater than
2× the 20-period average. Exit at 2% stop and 5% target.
```

```
Enter long when MACD crosses above its signal line and close is above
the 50-period SMA. Use a trailing stop of 1.5%.
```

5. Click **⚡ COMPILE & TEST**

The pipeline runs in the background (non-blocking):
- 🤖 **Step 1** → Gemini generates the Python class
- ⚙️ **Step 2** → Sandboxed `exec()` compiles and runs the backtest (60s timeout)
- ✅ **Results** → Win Rate, Return, Sharpe Ratio, Drawdown, Final Equity displayed

**Viewing history:**
- Every successful (and failed) run is automatically saved to `algo_history.json`
- The **📚 Strategy History** panel on the left lists all past runs
- **Click any card** to instantly reload that strategy's prompt, code, and results
- Click **CLEAR ALL** to wipe the history

---

## 🧪 Testing Without Kafka

You can test the **Algo Builder** tab entirely without Kafka or Docker:

```powershell
python run_demo.py --no-stream
```

This auto-downloads any missing data, then opens only the GUI. Skip straight to the **🧬 Algo Builder** tab.

To test the **sandbox runner** directly from Python:

```python
from sandbox_runner import run_dynamic_strategy

code = """
class GeneratedStrategy(Strategy):
    def init(self):
        close = self.data.Close
        self.ema_fast = self.I(lambda x, n: pd.Series(x).ewm(span=n, adjust=False).mean().values, close, 9)
        self.ema_slow = self.I(lambda x, n: pd.Series(x).ewm(span=n, adjust=False).mean().values, close, 21)

    def next(self):
        if crossover(self.ema_fast, self.ema_slow):
            price = self.data.Close[-1]
            self.buy(sl=price * 0.98, tp=price * 1.04)
        elif crossover(self.ema_slow, self.ema_fast):
            self.position.close()
"""

result = run_dynamic_strategy(code, symbol="RELIANCE")
print(result)
```

---

## 📁 Project Structure

```
CPI/
├── dashboard_qt.py          # Main PyQt5 GUI — all tabs and threading
├── stream_parquet.py        # Kafka producer — replays parquet data
├── scanner.py               # Indicator computation (EMA, RSI, MACD, ATR)
├── backtester.py            # backtesting.py integration for pattern validation
├── edge_calculator.py       # Alternative edge runner (used by scanner)
├── sandbox_runner.py        # Secure exec() sandbox for AI-generated strategies
├── runtime_config.py        # Symbol list / focus selection persistence
├── env_loader.py            # .env file reader
├── run_demo.py              # Simple demo runner (no Kafka required)
│
├── patterns/
│   ├── base.py              # Abstract BasePattern class
│   ├── breakout.py          # VolumeBreakout pattern
│   └── engulfing.py         # BullishEngulfing pattern
│
├── adk_swarm/
│   ├── coordinator.py       # ADK runner — routes to agents, manages sessions
│   └── sub_agents/
│       ├── trade_analyst.py     # Evaluates tradeability and risk quality
│       ├── pattern_analyst/     # Analyses chart pattern context
│       ├── risk_analyst/        # Generates risk-specific commentary
│       └── algo_architect.py    # Translates plain English → Strategy Python class
│
├── parquet_data/            # One .parquet per NSE symbol (git-ignored)
├── historical_base.parquet  # Master 50-day historical file (auto-generated)
├── alerts.json              # Live alert store (auto-generated)
├── algo_history.json        # Algo Builder history (auto-generated)
├── runtime_selection.json   # Saved symbol selection (auto-generated)
│
├── docker-compose.yml       # Kafka + Kafka UI (KRaft mode, no Zookeeper)
├── requirements.txt         # Full pinned dependency list
└── .env                     # API keys (git-ignored — create this yourself)
```

---

## 🔧 Troubleshooting

| Problem | Fix |
|---|---|
| `NameError: __build_class__ not found` | Update to latest `sandbox_runner.py` — this was a known bug, now fixed |
| `ModuleNotFoundError: No module named 'backtesting'` | `pip install backtesting` |
| `ModuleNotFoundError: No module named 'lightweight_charts'` | `pip install lightweight-charts` |
| `RuntimeError: lightweight_charts QtChart failed to import` | Also install `pip install PyQtWebEngine` |
| `parquet_data/` is empty or missing | Run `python data.py` or `python run_demo.py --force-download` |
| Some symbols have no data after download | Yahoo Finance may have gaps; run `python data.py --missing-only` to retry |
| Kafka connection refused | Ensure Docker Desktop is running and `docker compose up -d` has been executed |
| AI Swarm returns "missing Google ADK credentials" | Check `.env` has `GOOGLE_API_KEY=...` set correctly |
| `historical_base.parquet` not found | Run `stream_parquet.py` at least once — it auto-generates this file |
| Algo Builder times out (60s) | The AI may have generated an infinite loop. Rephrase your strategy prompt. |
| Empty chart on startup | No parquet data for the selected symbol in `parquet_data/` |
| `EngineWorker` skipping all symbols | Candle buffer needs ≥50 candles — wait for the stream to fill up |

---

## 📝 Notes for Hackathon Demo

- **Recommended demo order:** Live Dashboard → click an alert card → deep-dive chat → switch to 🧬 Algo Builder tab
- **Best demo prompt:** *"Buy when the 9-EMA crosses above the 21-EMA. Use 1.5× ATR stop-loss and 3× ATR take-profit."*
- **Fast demo mode:** Set `STREAM_SPEED = 0.001` in `stream_parquet.py` to replay candles faster (current default is `0.5s`)
- The fallback alert system works even without a Gemini API key — it generates rule-based alerts so the UI is never empty
- `algo_history.json` persists across restarts — demo runs from earlier sessions will still appear in the history panel
