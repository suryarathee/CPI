"""
dashboard.py — Live Candlestick Dashboard
==========================================
Consumes from the same Kafka topic as analyzer.py (`nse_raw_ticks`),
aggregates raw ticks into OHLCV candles, and renders a live Plotly
candlestick chart with volume bars and pattern-detection alerts.

Run:
    streamlit run dashboard.py
"""

import json
import time
import threading
from collections import deque
from datetime import datetime, timezone

import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from confluent_kafka import Consumer, KafkaError

# ─────────────────────────────────────────────
#  CONFIGURATION
# ─────────────────────────────────────────────
KAFKA_BROKER = "localhost:9092"
TOPIC        = "nse_raw_ticks"
GROUP_ID     = "streamlit-dashboard"

# Maximum raw ticks to keep in memory
MAX_TICKS    = 10_000

# ─────────────────────────────────────────────
#  PAGE CONFIG
# ─────────────────────────────────────────────
st.set_page_config(
    page_title  = "CPI — Live Market Dashboard",
    page_icon   = "📈",
    layout      = "wide",
    initial_sidebar_state = "expanded",
)

# ─────────────────────────────────────────────
#  CUSTOM CSS  (dark, premium feel)
# ─────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');

  html, body, [class*="css"] {
      font-family: 'Inter', sans-serif;
      background-color: #0d1117;
      color: #e6edf3;
  }
  .stApp { background-color: #0d1117; }

  /* Sidebar */
  [data-testid="stSidebar"] {
      background: #161b22;
      border-right: 1px solid #30363d;
  }

  /* Metric cards */
  [data-testid="stMetric"] {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 16px 20px;
  }
  [data-testid="stMetricLabel"]  { color: #8b949e; font-size: 0.8rem; }
  [data-testid="stMetricValue"]  { color: #e6edf3; font-size: 1.6rem; font-weight: 700; }
  [data-testid="stMetricDelta"]  { font-size: 0.85rem; }

  /* Alert box */
  .alert-box {
      background: linear-gradient(135deg, #161b22, #1f2937);
      border: 1px solid #f59e0b;
      border-left: 4px solid #f59e0b;
      border-radius: 8px;
      padding: 14px 18px;
      margin-bottom: 8px;
      animation: fadeIn 0.3s ease;
  }
  .alert-box .alert-title {
      font-weight: 700;
      color: #f59e0b;
      font-size: 0.95rem;
  }
  .alert-box .alert-body {
      color: #d1d5db;
      font-size: 0.83rem;
      margin-top: 4px;
  }
  @keyframes fadeIn { from {opacity:0; transform:translateY(-4px);} to {opacity:1; transform:translateY(0);} }

  /* Header */
  .dash-header {
      background: linear-gradient(90deg, #0d1117, #1a237e22);
      border-bottom: 1px solid #30363d;
      padding: 18px 0 12px 0;
      margin-bottom: 18px;
  }
  .dash-title {
      font-size: 1.8rem;
      font-weight: 700;
      background: linear-gradient(90deg, #58a6ff, #bc8cff);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
  }
  .dash-sub {
      color: #8b949e;
      font-size: 0.85rem;
  }
  .live-dot {
      display: inline-block;
      width: 9px; height: 9px;
      background: #3fb950;
      border-radius: 50%;
      margin-right: 6px;
      animation: pulse 1.4s infinite;
  }
  @keyframes pulse {
      0%   { box-shadow: 0 0 0 0 rgba(63,185,80,.6); }
      70%  { box-shadow: 0 0 0 8px rgba(63,185,80,0); }
      100% { box-shadow: 0 0 0 0 rgba(63,185,80,0); }
  }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────
#  SHARED STATE  (thread-safe via a lock)
# ─────────────────────────────────────────────
if "tick_buffer" not in st.session_state:
    st.session_state.tick_buffer = deque(maxlen=MAX_TICKS)
if "alerts" not in st.session_state:
    st.session_state.alerts = deque(maxlen=20)
if "consumer_running" not in st.session_state:
    st.session_state.consumer_running = False
if "lock" not in st.session_state:
    st.session_state.lock = threading.Lock()

# ─────────────────────────────────────────────
#  KAFKA CONSUMER THREAD
# ─────────────────────────────────────────────
PATTERN_WINDOW = 50

def _pattern_check(tick_buffer: deque, alerts: deque, lock: threading.Lock):
    """Replicates analyzer.py pattern logic and pushes alerts."""
    if len(tick_buffer) < PATTERN_WINDOW:
        return
    df = pd.DataFrame(list(tick_buffer)[-PATTERN_WINDOW:])
    current_price = df.iloc[-1]["price"]
    current_vol   = df.iloc[-1]["volume"]
    avg_vol       = df.iloc[:-1]["volume"].mean()
    max_price     = df.iloc[:-1]["price"].max()

    if current_price > max_price and current_vol > (avg_vol * 3):
        ts    = datetime.now().strftime("%H:%M:%S")
        sym   = df.iloc[-1]["symbol"]
        alert = {
            "time":  ts,
            "symbol": sym,
            "price":  current_price,
            "max_price": max_price,
            "volume":    current_vol,
        }
        with lock:
            alerts.appendleft(alert)


def kafka_consumer_thread(tick_buffer: deque, alerts: deque, lock: threading.Lock,
                           stop_event: threading.Event):
    """Background thread: poll Kafka → fill tick_buffer → detect patterns."""
    conf = {
        "bootstrap.servers": KAFKA_BROKER,
        "group.id":          GROUP_ID,
        "auto.offset.reset": "latest",
    }
    consumer = Consumer(conf)
    consumer.subscribe([TOPIC])

    while not stop_event.is_set():
        msg = consumer.poll(timeout=0.5)
        if msg is None:
            continue
        if msg.error():
            if msg.error().code() != KafkaError._PARTITION_EOF:
                pass          # swallow transient errors
            continue
        try:
            raw = json.loads(msg.value().decode("utf-8"))
            tick = {
                "ts":     time.time(),
                "symbol": raw["symbol"],
                "price":  float(raw["price"]),
                "volume": float(raw["volume"]),
            }
            with lock:
                tick_buffer.append(tick)
            _pattern_check(tick_buffer, alerts, lock)
        except Exception:
            pass

    consumer.close()


# ─────────────────────────────────────────────
#  START CONSUMER (once per session)
# ─────────────────────────────────────────────
if not st.session_state.consumer_running:
    stop_event = threading.Event()
    st.session_state.stop_event = stop_event
    t = threading.Thread(
        target=kafka_consumer_thread,
        args=(
            st.session_state.tick_buffer,
            st.session_state.alerts,
            st.session_state.lock,
            stop_event,
        ),
        daemon=True,
    )
    t.start()
    st.session_state.consumer_running = True


# ─────────────────────────────────────────────
#  OHLCV AGGREGATION
# ─────────────────────────────────────────────
def build_ohlcv(tick_buffer: deque, lock: threading.Lock,
                interval_sec: int) -> pd.DataFrame:
    """Aggregate raw ticks into OHLCV candles."""
    with lock:
        ticks = list(tick_buffer)

    if not ticks:
        return pd.DataFrame()

    df = pd.DataFrame(ticks)
    df["datetime"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    df = df.set_index("datetime").sort_index()

    rule = f"{interval_sec}s"
    ohlcv = df["price"].resample(rule).ohlc()
    ohlcv["volume"] = df["volume"].resample(rule).sum()
    ohlcv = ohlcv.dropna()
    return ohlcv


# ─────────────────────────────────────────────
#  PLOTLY CHART
# ─────────────────────────────────────────────
def build_chart(ohlcv: pd.DataFrame, symbol: str) -> go.Figure:
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.04,
        row_heights=[0.72, 0.28],
    )

    # — Candlestick —
    fig.add_trace(
        go.Candlestick(
            x     = ohlcv.index,
            open  = ohlcv["open"],
            high  = ohlcv["high"],
            low   = ohlcv["low"],
            close = ohlcv["close"],
            name  = symbol,
            increasing_line_color  = "#3fb950",
            decreasing_line_color  = "#f85149",
            increasing_fillcolor   = "#3fb950",
            decreasing_fillcolor   = "#f85149",
            whiskerwidth=0,
        ),
        row=1, col=1,
    )

    # — 10-candle SMA overlay —
    if len(ohlcv) >= 10:
        sma = ohlcv["close"].rolling(10).mean()
        fig.add_trace(
            go.Scatter(
                x=ohlcv.index, y=sma,
                mode="lines",
                line=dict(color="#bc8cff", width=1.5, dash="dot"),
                name="SMA-10",
                opacity=0.85,
            ),
            row=1, col=1,
        )

    # — Volume bars —
    colors = [
        "#3fb950" if c >= o else "#f85149"
        for o, c in zip(ohlcv["open"], ohlcv["close"])
    ]
    fig.add_trace(
        go.Bar(
            x=ohlcv.index, y=ohlcv["volume"],
            marker_color=colors,
            name="Volume",
            opacity=0.65,
        ),
        row=2, col=1,
    )

    fig.update_layout(
        paper_bgcolor = "#0d1117",
        plot_bgcolor  = "#0d1117",
        font          = dict(family="Inter", color="#8b949e", size=12),
        margin        = dict(l=10, r=10, t=10, b=10),
        legend        = dict(
            orientation="h", yanchor="bottom", y=1.01,
            bgcolor="rgba(0,0,0,0)", font_color="#8b949e",
        ),
        xaxis_rangeslider_visible=False,
    )
    fig.update_xaxes(
        showgrid=True,  gridcolor="#21262d", zeroline=False,
        tickfont=dict(size=10),
    )
    fig.update_yaxes(
        showgrid=True,  gridcolor="#21262d", zeroline=False,
        tickfont=dict(size=10), tickprefix="$",
        row=1, col=1,
    )
    fig.update_yaxes(
        showgrid=True, gridcolor="#21262d", zeroline=False,
        tickfont=dict(size=10),
        row=2, col=1,
    )
    return fig


# ─────────────────────────────────────────────
#  SIDEBAR CONTROLS
# ─────────────────────────────────────────────
with st.sidebar:
    st.markdown("### ⚙️ Controls")

    refresh_sec = st.slider("Refresh interval (s)", 1, 10, 2)

    interval_options = {
        "1 second":  1,
        "5 seconds": 5,
        "15 seconds": 15,
        "30 seconds": 30,
        "1 minute":  60,
    }
    candle_label = st.selectbox("Candle interval", list(interval_options.keys()), index=1)
    candle_sec   = interval_options[candle_label]

    st.markdown("---")
    st.markdown("#### 📡 Pipeline Status")
    with st.session_state.lock:
        tick_count = len(st.session_state.tick_buffer)
    st.metric("Ticks buffered", tick_count)

    st.markdown("---")
    st.markdown(
        "<span style='color:#8b949e;font-size:0.78rem;'>"
        "Data flows:<br>"
        "Binance WS → C++ Ingester → Kafka<br>"
        "↳ <code>nse_raw_ticks</code> → Dashboard"
        "</span>",
        unsafe_allow_html=True,
    )

# ─────────────────────────────────────────────
#  MAIN LAYOUT
# ─────────────────────────────────────────────
st.markdown(
    """
    <div class="dash-header">
      <div class="dash-title">📈 CPI — Live Market Dashboard</div>
      <div class="dash-sub">
        <span class="live-dot"></span>
        Live · Binance BTC/USDT · via Kafka
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Build OHLCV
ohlcv = build_ohlcv(st.session_state.tick_buffer, st.session_state.lock, candle_sec)

# ── Metrics row ──────────────────────────────
col1, col2, col3, col4 = st.columns(4)
if not ohlcv.empty:
    last  = ohlcv.iloc[-1]
    prev  = ohlcv.iloc[-2] if len(ohlcv) > 1 else last
    delta = last["close"] - prev["close"]
    pct   = (delta / prev["close"] * 100) if prev["close"] else 0
    col1.metric("Last Price",  f"${last['close']:,.2f}", f"{delta:+.2f} ({pct:+.2f}%)")
    col2.metric("24h High",    f"${ohlcv['high'].max():,.2f}")
    col3.metric("24h Low",     f"${ohlcv['low'].min():,.2f}")
    col4.metric("Candles",     len(ohlcv))
else:
    col1.metric("Last Price",  "—")
    col2.metric("24h High",    "—")
    col3.metric("24h Low",     "—")
    col4.metric("Candles",     0)

st.markdown("<br>", unsafe_allow_html=True)

# ── Chart ────────────────────────────────────
chart_placeholder = st.empty()

if not ohlcv.empty:
    fig = build_chart(ohlcv, "BTCUSDT")
    chart_placeholder.plotly_chart(fig, use_container_width=True,
                                   config={"displayModeBar": False})
else:
    chart_placeholder.info(
        "⏳ Waiting for live data…\n\n"
        "Make sure Kafka is running (`docker compose up`) "
        "and the C++ ingester is streaming."
    )

# ── Alerts panel ─────────────────────────────
with st.session_state.lock:
    alerts_snapshot = list(st.session_state.alerts)

st.markdown("### 🚨 Pattern Alerts — Bullish Micro-Breakout")
if alerts_snapshot:
    for a in alerts_snapshot[:8]:
        st.markdown(
            f"""
            <div class="alert-box">
              <div class="alert-title">🚨 {a['symbol']} · {a['time']}</div>
              <div class="alert-body">
                Broke above resistance <strong>${a['max_price']:.2f}</strong> →
                closed at <strong>${a['price']:.2f}</strong> ·
                Volume spike: <strong>{a['volume']:.4f} BTC</strong>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
else:
    st.markdown(
        "<p style='color:#8b949e;font-size:0.85rem;'>No patterns detected yet.</p>",
        unsafe_allow_html=True,
    )

# ── Auto-refresh ──────────────────────────────
time.sleep(refresh_sec)
st.rerun()
