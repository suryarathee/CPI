from __future__ import annotations

import json
import os
import sys
import uuid
from datetime import datetime

import pandas as pd
from confluent_kafka import Consumer
from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QPushButton,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QScrollArea,
)

from backtester import run_historical_backtest
from patterns import BullishEngulfing, VolumeBreakout
from runtime_config import available_symbols, load_selection, save_selection
from adk_swarm import generate_ui_alert, process_deep_dive_query, generate_algo_code
from sandbox_runner import run_dynamic_strategy

try:
    from lightweight_charts.widgets import QtChart
except Exception as exc:
    QtChart = None
    QTCHART_IMPORT_ERROR = exc
else:
    QTCHART_IMPORT_ERROR = None


KAFKA_BROKER = "localhost:9092"
TOPIC = "nse_live_candles"
ALERTS_FILE = "alerts.json"
ALGO_HISTORY_FILE = "algo_history.json"
START_SIGNAL_FILE = ".stream_start.signal"

T = {
    "bg": "#0A0C0F",
    "panel": "#0E1117",
    "surface": "#161B24",
    "surface2": "#1A2030",
    "border": "#1E2535",
    "border_hi": "#2A3550",
    "text": "#C8CDD8",
    "text_dim": "#718096",
    "text_bright": "#FFFFFF",
    "accent": "#FF5733",
    "accent2": "#00BFA5",
    "bull": "#00C896",
    "bear": "#EF5350",
    "warn": "#F7C948",
    "vol_bull": "#00332E",
    "vol_bear": "#3D1111",
    "ema_fast": "#F7C948",
    "ema_slow": "#A78BFA",
}

# --- NEW COMPONENTS FOR INTERACTIVE AI ---

class DeepDiveWorker(QThread):
    """
    Handles follow-up LLM queries in a non-blocking background thread.
    """
    response_ready = pyqtSignal(str)

    def __init__(self, query: str, session_id: str, agent_type: str, model_name: str):
        super().__init__()
        self.query = query
        self.session_id = session_id
        self.agent_type = agent_type
        self.model_name = model_name

    def run(self):
        try:
            response = process_deep_dive_query(self.query, self.session_id, self.agent_type, self.model_name)
            self.response_ready.emit(response)
        except Exception as e:
            self.response_ready.emit(f"Worker Error: {e}")

class AlertCard(QFrame):
    """
    A clickable alert widget that stores metadata and triggers deep-dive chat.
    """
    clicked = pyqtSignal(dict)

    def __init__(self, alert_data: dict):
        super().__init__()
        self.alert_data = alert_data
        self.setObjectName("alertCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)
        self.setStyleSheet(f"""
            QFrame#alertCard {{
                background-color: {T['surface']};
                border-left: 4px solid {T['accent']};
                border-radius: 4px;
                margin-bottom: 2px;
            }}
            QFrame#alertCard:hover {{
                background-color: {T['surface2']};
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(6)

        header = QLabel(f"{alert_data.get('symbol', 'ALERT')} [{alert_data.get('pattern', 'SIGNAL')}]")
        header.setStyleSheet(f"color: {T['accent']}; font-weight: bold; font-size: 14px;")
        layout.addWidget(header)

        time_lbl = QLabel(f"Mode: {alert_data.get('agent_mode', 'Coordinator')} | {alert_data.get('timestamp', 'Now')}")
        time_lbl.setStyleSheet(f"color: {T['text_dim']}; font-size: 11px;")
        layout.addWidget(time_lbl)

        synthesis = QLabel(alert_data.get('synthesis', 'Assessment in progress...'))
        synthesis.setWordWrap(True)
        synthesis.setStyleSheet(f"color: {T['text']}; font-size: 13px; line-height: 1.4;")
        layout.addWidget(synthesis)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.alert_data)

# --- END NEW COMPONENTS ---


class StrategyHistoryCard(QFrame):
    """
    A clickable card representing a previously built strategy.
    Emits `clicked` with the full history entry dict when pressed.
    """
    clicked = pyqtSignal(dict)

    def __init__(self, entry: dict):
        super().__init__()
        self.entry = entry
        self.setObjectName("historyCard")
        self.setFrameShape(QFrame.StyledPanel)
        self.setCursor(Qt.PointingHandCursor)

        ok = entry.get("stats", {}).get("ok", False)
        wr = entry.get("stats", {}).get("win_rate_pct", 0.0)
        ret = entry.get("stats", {}).get("return_pct", 0.0)
        ret_color = T["bull"] if ret >= 0 else T["bear"]
        border_color = T["bull"] if ok else T["text_dim"]

        self.setStyleSheet(f"""
            QFrame#historyCard {{
                background-color: {T['surface']};
                border-left: 3px solid {border_color};
                border-radius: 4px;
                margin-bottom: 2px;
            }}
            QFrame#historyCard:hover {{
                background-color: {T['surface2']};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(3)

        # Title row: symbol + timestamp
        title_row = QHBoxLayout()
        sym_lbl = QLabel(f"{entry.get('symbol', '?')}")
        sym_lbl.setStyleSheet(f"color:{T['accent']}; font-weight:bold; font-size:15px;")
        ts_lbl = QLabel(entry.get("timestamp", "")[:16].replace("T", " "))
        ts_lbl.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        title_row.addWidget(sym_lbl)
        title_row.addStretch()
        title_row.addWidget(ts_lbl)
        layout.addLayout(title_row)

        # Prompt snippet
        prompt_snip = entry.get("prompt", "")[:70].replace("\n", " ")
        if len(entry.get("prompt", "")) > 70:
            prompt_snip += "..."
        prompt_lbl = QLabel(prompt_snip)
        prompt_lbl.setWordWrap(True)
        prompt_lbl.setStyleSheet(f"color:{T['text']}; font-size:13px;")
        layout.addWidget(prompt_lbl)

        # Stats row
        if ok:
            stats_lbl = QLabel(
                f"WR: {wr:.1f}%  |  Return: "
                f"<span style='color:{ret_color};'>{ret:+.2f}%</span>"
            )
            stats_lbl.setTextFormat(Qt.RichText)
        else:
            stats_lbl = QLabel("⚠ Compile/run error")
        stats_lbl.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        layout.addWidget(stats_lbl)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.entry)


class AlgoBuilderWorker(QThread):
    """
    Background thread for the Text-to-Algo pipeline:
      1. Calls the Algo-Architect LLM agent to generate Python code.
      2. Passes the code to sandbox_runner for safe execution.
      3. Emits the final result (stats or error) back to the UI.
    """
    code_ready = pyqtSignal(str)          # emits the generated Python code
    result_ready = pyqtSignal(dict)       # emits backtest stats or error dict
    status_update = pyqtSignal(str)       # emits step-by-step status messages

    def __init__(self, prompt: str, symbol: str, model_name: str):
        super().__init__()
        self.prompt = prompt
        self.symbol = symbol
        self.model_name = model_name

    def run(self):
        try:
            # ── Step 1: LLM code generation ───────────────────────────────
            self.status_update.emit("🤖 Algo-Architect is generating your strategy...")
            code = generate_algo_code(self.prompt, model_name=self.model_name)
            self.code_ready.emit(code)

            # ── Step 2: Sandbox execution ─────────────────────────────────
            self.status_update.emit(f"⚙️  Compiling & backtesting on {self.symbol}...")
            stats = run_dynamic_strategy(code, symbol=self.symbol)

            self.result_ready.emit(stats)
            if stats.get("ok"):
                self.status_update.emit("✅ Backtest complete!")
            else:
                self.status_update.emit(f"❌ Error: {stats.get('error', 'Unknown')}")

        except Exception as exc:
            self.result_ready.emit({"ok": False, "error": str(exc), "traceback": ""})
            self.status_update.emit(f"❌ Worker crashed: {exc}")


STYLESHEET = f"""
QMainWindow {{ background-color: {T['bg']}; }}
QWidget {{
    background-color: transparent;
    color: {T['text']};
    font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace;
    font-size: 16px;
}}
QPushButton {{
    background-color: {T['surface']};
    color: "#FFFFFF";
    border: 1px solid {T['border']};
    border-radius: 4px;
    padding: 9px 18px;
    font-size: 15px;
}}
QPushButton:hover {{
    border-color: {T['border_hi']};
    color: {T['text_bright']};
}}
QPushButton#accentBtn {{
    background-color: #FFFFFF;
    color: #FFFFFF;
    border: none;
    font-weight: bold;
}}
QTextEdit, QListWidget, QComboBox {{
    background-color: {T['surface']};
    border: 1px solid {T['border']};
    color: {T['text']};
    padding: 5px;
    font-size: 15px;
}}
QTabWidget::pane {{
    border: 1px solid {T['border']};
    background: {T['bg']};
}}
QTabBar::tab {{
    background: {T['surface']};
    color: {T['text_dim']};
    border: 1px solid {T['border']};
    padding: 12px 24px;
    min-width: 160px;
    font-size: 15px;
}}
QTabBar::tab:selected {{
    background: {T['panel']};
    color: {T['text_bright']};
    border-color: {T['border_hi']};
}}
QTableWidget {{
    background-color: {T['surface']};
    border: 1px solid {T['border']};
    color: {T['text']};
    gridline-color: {T['border']};
    selection-background-color: {T['surface2']};
    font-size: 15px;
}}
QHeaderView::section {{
    background-color: {T['panel']};
    color: {T['text_bright']};
    border: 1px solid {T['border']};
    padding: 10px;
    font-size: 15px;
}}
"""


class KafkaWorker(QThread):
    new_candle_signal = pyqtSignal(dict)

    def run(self):
        consumer = Consumer(
            {
                "bootstrap.servers": KAFKA_BROKER,
                "group.id": "pyqt-terminal-v4",
                "auto.offset.reset": "latest",
            }
        )
        consumer.subscribe([TOPIC])
        try:
            while True:
                msg = consumer.poll(0.5)
                if msg is None or msg.error():
                    continue
                try:
                    raw = json.loads(msg.value().decode("utf-8"))
                except Exception:
                    continue
                if raw.get("symbol"):
                    self.new_candle_signal.emit(raw)
        finally:
            consumer.close()


class EngineWorker(QThread):
    """
    The Elite Orchestration Engine. 
    Handles multi-symbol scanning, backtest validation, and Agentic reasoning.
    """
    status_signal = pyqtSignal(str)
    alert_ready_signal = pyqtSignal(dict)

    def __init__(self, terminal: TradingTerminal):
        super().__init__()
        self.terminal = terminal
        self.is_running = True

    def run(self):
        while self.is_running:
            if not self.terminal.stream_started:
                self.msleep(1000)
                continue

            symbols = self.terminal.selected_symbols
            for symbol in symbols:
                try:
                    # 1. SCANNING STATE
                    self.status_signal.emit(f"Scanning {symbol}...")
                    data = self.terminal.candle_data.get(symbol, [])
                    if len(data) < 50:
                        continue
                    
                    df = pd.DataFrame(data)
                    triggered_pattern = None
                    for pattern in self.terminal.active_patterns:
                        res = pattern.evaluate(
                            df['open'].tolist(),
                            df['high'].tolist(),
                            df['low'].tolist(),
                            df['close'].tolist(),
                            df['volume'].tolist()
                        )
                        if res:
                            triggered_pattern = pattern
                            break
                    
                    if not triggered_pattern:
                        continue

                    # 2. BACKTESTING STATE (Pre-filtering)
                    self.status_signal.emit(f"Validating {symbol} {triggered_pattern.name}...")
                    edge = run_historical_backtest(symbol, triggered_pattern)
                    
                    # Agentic Pre-filter: Only proceed if expectancy is positive or win rate > 40%
                    if edge["expectancy_pct"] < 0 and edge["win_rate"] < 0.40:
                        self.status_signal.emit(f"Skipping {symbol}: Low historical expectancy.")
                        continue

                    # 3. REASONING STATE (AI Swarm)
                    self.status_signal.emit(f"Consulting Swarm for {symbol}...")
                    agent_type = self.terminal.agent_combo.currentText()
                    model_name = self.terminal.model_combo.currentText()
                    
                    context = {
                        "symbol": symbol,
                        "price": float(df.iloc[-1]["close"]),
                        "pattern_name": triggered_pattern.name,
                        "edge": edge,
                        "indicator_context": {
                            "volume_surge": float(df.iloc[-1]["volume"] / df["volume"].tail(20).mean())
                        }
                    }
                    
                    alert_text, session_id = generate_ui_alert(context, agent_type=agent_type, model_name=model_name)
                    
                    bullets = [b.strip("- ") for b in alert_text.split("\n") if b.strip()]
                    new_alert = {
                        "event_id": f"{symbol}:{df.iloc[-1]['time']}",
                        "symbol": symbol,
                        "pattern": triggered_pattern.name,
                        "timestamp": str(df.iloc[-1]["time"]),
                        "agent_mode": agent_type,
                        "session_id": session_id,
                        "quant": bullets[0] if len(bullets) > 0 else "Analysis completed.",
                        "risk": bullets[1] if len(bullets) > 1 else "Check metrics.",
                        "synthesis": bullets[2] if len(bullets) > 2 else alert_text
                    }
                    
                    if agent_type != "Coordinator" and bullets:
                        new_alert["synthesis"] = alert_text
                        new_alert["quant"] = f"Agent {agent_type} assessment."
                        new_alert["risk"] = "See synthesis."

                    self._write_alert(new_alert)
                    self.status_signal.emit(f"Alert emitted for {symbol}!")

                except Exception as e:
                    print(f"[ENGINE ERROR] {symbol}: {e}")
            
            self.status_signal.emit("Engine Idle")
            self.msleep(5000)

    def _write_alert(self, new_alert):
        try:
            alerts = []
            if os.path.exists(ALERTS_FILE):
                with open(ALERTS_FILE, "r", encoding="utf-8") as f:
                    alerts = json.load(f)
            if any(a.get("event_id") == new_alert["event_id"] for a in alerts):
                return
            alerts.append(new_alert)
            with open(ALERTS_FILE, "w", encoding="utf-8") as f:
                json.dump(alerts, f, indent=2)
        except Exception as exc:
            print(f"[ENGINE FILE ERROR]: {exc}")


class TradingTerminal(QMainWindow):
    def __init__(self):
        super().__init__()

        if QtChart is None:
            raise RuntimeError(f"lightweight_charts QtChart failed to import: {QTCHART_IMPORT_ERROR}")

        selection = load_selection()
        self.available_symbols = available_symbols()
        self.selected_symbols = selection["selected_symbols"]
        self.focus_symbol = selection["focus_symbol"]

        self.candle_data = {} # Multi-symbol support: {symbol: [candles]}
        self.seen_alert_ids = set()
        self.stream_started = False
        self.active_patterns = [VolumeBreakout(), BullishEngulfing()]
        self.active_session_id = None

        self.setWindowTitle(f"Kinetic Monolith - {self.focus_symbol}")
        self.showMaximized()
        self.setStyleSheet(STYLESHEET)

        self._build_ui()
        self._populate_symbol_controls()
        self.load_historical_base()

        self.kafka_thread = KafkaWorker()
        self.kafka_thread.new_candle_signal.connect(self.update_chart)
        self.kafka_thread.start()

        self.engine_worker = EngineWorker(self)
        self.engine_worker.status_signal.connect(self.status_label.setText)
        self.engine_worker.start()

        self.alert_timer = QTimer()
        self.alert_timer.timeout.connect(self.check_ai_alerts)
        self.alert_timer.start(1000)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header = QWidget()
        header.setFixedHeight(72)
        header.setStyleSheet(f"background-color:{T['panel']}; border-bottom:1px solid {T['border']};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(20, 0, 20, 0)

        self.title_label = QLabel("Agent Scanner")
        self.title_label.setStyleSheet(
            f"color:{T['text_bright']}; font-size:20px; font-weight:bold; letter-spacing:1px;"
        )
        header_layout.addWidget(self.title_label)

        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        header_layout.addWidget(self.status_label)
        header_layout.addStretch()

        self.start_button = QPushButton("START TEST")
        self.start_button.setObjectName("accentBtn")
        self.start_button.clicked.connect(self.start_test_run)
        header_layout.addWidget(self.start_button)

        root_layout.addWidget(header)

        self.main_tabs = QTabWidget()
        root_layout.addWidget(self.main_tabs, 1)

        live_tab = QWidget()
        live_tab.setStyleSheet(f"background-color:{T['bg']};")
        self.main_tabs.addTab(live_tab, "Live Dashboard")

        middle_layout = QHBoxLayout(live_tab)
        middle_layout.setContentsMargins(0, 0, 0, 0)
        middle_layout.setSpacing(0)

        chart_column = QWidget()
        chart_layout = QVBoxLayout(chart_column)
        chart_layout.setContentsMargins(0, 0, 0, 0)
        chart_layout.setSpacing(0)
        middle_layout.addWidget(chart_column, 1)

        chart_header = QWidget()
        chart_header.setFixedHeight(50)
        chart_header.setStyleSheet(f"background-color:{T['panel']}; border-bottom:1px solid {T['border']};")
        chart_header_layout = QHBoxLayout(chart_header)
        chart_header_layout.setContentsMargins(15, 0, 15, 0)

        self.chart_symbol_label = QLabel(self.focus_symbol)
        self.chart_symbol_label.setStyleSheet(f"color:{T['text_bright']}; font-size:16px; font-weight:bold;")
        chart_header_layout.addWidget(self.chart_symbol_label)

        self.chart_price_label = QLabel("--")
        self.chart_price_label.setStyleSheet(f"color:{T['accent']}; font-size:16px; font-weight:bold;")
        chart_header_layout.addWidget(self.chart_price_label)
        chart_header_layout.addStretch()

        self.chart_info_label = QLabel("Waiting for stream")
        self.chart_info_label.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        chart_header_layout.addWidget(self.chart_info_label)

        chart_layout.addWidget(chart_header)

        self.chart_host = QWidget()
        self.chart_host.setStyleSheet(f"background-color:{T['panel']};")
        chart_host_layout = QVBoxLayout(self.chart_host)
        chart_host_layout.setContentsMargins(0, 0, 0, 0)

        self.qt_chart = QtChart(self.chart_host)
        self.qt_chart.layout(
            background_color=T["panel"],
            text_color=T["text_dim"],
            font_family="Consolas",
            font_size=14,
        )
        self.qt_chart.grid(vert_enabled=True, horz_enabled=True, color="rgba(30,37,53,0.6)")
        self.qt_chart.candle_style(up_color=T["bull"], down_color=T["bear"])
        self.qt_chart.volume_config(up_color=T["vol_bull"], down_color=T["vol_bear"])
        self.qt_chart.legend(
            visible=True,
            lines=True,
            ohlc=True,
            percent=True,
            color=T["text"],
            font_family="Consolas",
            color_based_on_candle=True,
        )
        self.ema_fast_line = self.qt_chart.create_line(
            name="EMA 9", color=T["ema_fast"], width=2, price_line=False, price_label=False
        )
        self.ema_slow_line = self.qt_chart.create_line(
            name="EMA 21", color=T["ema_slow"], width=2, price_line=False, price_label=False
        )
        chart_host_layout.addWidget(self.qt_chart.get_webview())
        chart_layout.addWidget(self.chart_host, 1)

        right_panel = QWidget()
        right_panel.setFixedWidth(420)
        right_panel.setStyleSheet(f"background-color:{T['panel']}; border-left:1px solid {T['border']};")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(15, 15, 15, 15)
        right_layout.setSpacing(12)
        middle_layout.addWidget(right_panel)

        # ── Right panel: pure Live Alerts (no inner tab widget) ──────────
        scanner_label = QLabel("Scanner Controls")
        scanner_label.setStyleSheet(f"color:{T['text_bright']}; font-size:16px; font-weight:bold;")
        right_layout.addWidget(scanner_label)

        scanner_hint = QLabel("Select symbols to scan in parallel. Focus controls the main chart.")
        scanner_hint.setWordWrap(True)
        scanner_hint.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        right_layout.addWidget(scanner_hint)

        agent_row = QWidget()
        agent_layout = QHBoxLayout(agent_row)
        agent_layout.setContentsMargins(0, 0, 0, 0)
        agent_layout.setSpacing(6)
        agent_label = QLabel("Agent Type")
        agent_label.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        agent_layout.addWidget(agent_label)
        self.agent_combo = QComboBox()
        self.agent_combo.addItems(["Coordinator", "Trade Analyst", "Pattern Analyst", "Risk Analyst"])
        agent_layout.addWidget(self.agent_combo, 1)
        right_layout.addWidget(agent_row)

        model_row = QWidget()
        model_layout = QHBoxLayout(model_row)
        model_layout.setContentsMargins(0, 0, 0, 0)
        model_layout.setSpacing(6)
        model_label = QLabel("LLM Model")
        model_label.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        model_layout.addWidget(model_label)
        self.model_combo = QComboBox()
        self.model_combo.addItems([
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-1.5-flash-8b",
            "gemini-3-flash-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash"
        ])
        model_layout.addWidget(self.model_combo, 1)
        right_layout.addWidget(model_row)

        self.symbol_list = QListWidget()
        self.symbol_list.setSelectionMode(QListWidget.MultiSelection)
        self.symbol_list.itemSelectionChanged.connect(self._sync_focus_options)
        right_layout.addWidget(self.symbol_list)

        focus_row = QWidget()
        focus_layout = QHBoxLayout(focus_row)
        focus_layout.setContentsMargins(0, 0, 0, 0)
        focus_layout.setSpacing(6)
        focus_label = QLabel("Focus")
        focus_label.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        focus_layout.addWidget(focus_label)
        self.focus_combo = QComboBox()
        focus_layout.addWidget(self.focus_combo, 1)
        right_layout.addWidget(focus_row)

        button_row = QWidget()
        button_layout = QHBoxLayout(button_row)
        button_layout.setContentsMargins(0, 0, 0, 0)
        button_layout.setSpacing(6)
        self.apply_button = QPushButton("APPLY SAMPLE")
        self.apply_button.setObjectName("accentBtn")
        self.apply_button.clicked.connect(self.apply_symbol_selection)
        button_layout.addWidget(self.apply_button)
        self.selection_status = QLabel("")
        self.selection_status.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        button_layout.addWidget(self.selection_status)
        button_layout.addStretch()
        right_layout.addWidget(button_row)

        alerts_header = QWidget()
        alerts_header_layout = QHBoxLayout(alerts_header)
        alerts_header_layout.setContentsMargins(0, 0, 0, 0)
        alerts_header_layout.setSpacing(6)
        alerts_label = QLabel("Swarm Alerts")
        alerts_label.setStyleSheet(f"color:{T['text_bright']}; font-size:16px; font-weight:bold;")
        alerts_header_layout.addWidget(alerts_label)
        alerts_header_layout.addStretch()
        self.clear_alerts_button = QPushButton("CLEAR")
        self.clear_alerts_button.clicked.connect(self.clear_alert_history)
        alerts_header_layout.addWidget(self.clear_alerts_button)
        right_layout.addWidget(alerts_header)

        self.alerts_scroll = QScrollArea()
        self.alerts_scroll.setWidgetResizable(True)
        self.alerts_scroll.setStyleSheet("border: none; background-color: transparent;")
        self.alerts_container = QWidget()
        self.alerts_layout = QVBoxLayout(self.alerts_container)
        self.alerts_layout.setContentsMargins(0, 0, 0, 0)
        self.alerts_layout.setSpacing(10)
        self.alerts_layout.addStretch()
        self.alerts_scroll.setWidget(self.alerts_container)
        right_layout.addWidget(self.alerts_scroll, 1)

        # Agent Chat Panel (Hidden by default)
        self.chat_panel = QFrame()
        self.chat_panel.setFixedWidth(420)
        self.chat_panel.setStyleSheet(f"background-color:{T['surface']}; border-left:2px solid {T['accent']};")
        self.chat_layout = QVBoxLayout(self.chat_panel)
        
        chat_header = QHBoxLayout()
        self.chat_title = QLabel("Agent Deep-Dive")
        self.chat_title.setStyleSheet("font-weight: bold; color: #FFFFFF;")
        close_chat = QPushButton("×")
        close_chat.setFixedSize(30, 30)
        close_chat.clicked.connect(lambda: self.chat_panel.hide())
        chat_header.addWidget(self.chat_title)
        chat_header.addStretch()
        chat_header.addWidget(close_chat)
        self.chat_layout.addLayout(chat_header)

        self.chat_history = QTextEdit()
        self.chat_history.setReadOnly(True)
        self.chat_layout.addWidget(self.chat_history)

        self.chat_input = QTextEdit()
        self.chat_input.setFixedHeight(60)
        self.chat_input.setPlaceholderText("Ask follow-up about this setup...")
        self.chat_layout.addWidget(self.chat_input)

        send_btn = QPushButton("ASK AGENT")
        send_btn.setObjectName("accentBtn")
        send_btn.clicked.connect(self.send_chat_query)
        self.chat_layout.addWidget(send_btn)

        self.chat_panel.hide()
        middle_layout.addWidget(self.chat_panel)

        self._build_backtesting_tab()
        self._build_algo_builder_tab()

    def _build_backtesting_tab(self):
        backtest_tab = QWidget()
        backtest_tab.setStyleSheet(f"background-color:{T['bg']};")
        self.main_tabs.addTab(backtest_tab, "Backtesting")

        layout = QVBoxLayout(backtest_tab)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        heading = QLabel("Historical Backtest Metrics")
        heading.setStyleSheet(f"color:{T['text_bright']}; font-size:22px; font-weight:bold;")
        layout.addWidget(heading)

        subtitle = QLabel(
            "This uses the same pattern logic as the scanner. A trade is counted when a signal hits, "
            "then either the 2% target or 1% stop is reached later in the historical data."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        layout.addWidget(subtitle)

        summary_frame = QFrame()
        summary_frame.setStyleSheet(
            f"background-color:{T['panel']}; border:1px solid {T['border']}; border-radius:6px;"
        )
        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(15, 15, 15, 15)
        summary_layout.setSpacing(8)

        self.backtest_summary_label = QLabel("No backtest data loaded yet.")
        self.backtest_summary_label.setWordWrap(True)
        self.backtest_summary_label.setStyleSheet(f"color:{T['text']}; font-size:15px;")
        summary_layout.addWidget(self.backtest_summary_label)

        metrics_hint = QLabel(
            "Key metrics: win rate, total trades, expectancy per trade, average bars to exit, and streaks."
        )
        metrics_hint.setWordWrap(True)
        metrics_hint.setStyleSheet(f"color:{T['text_dim']}; font-size:12px;")
        summary_layout.addWidget(metrics_hint)
        layout.addWidget(summary_frame)

        controls_row = QWidget()
        controls_layout = QHBoxLayout(controls_row)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        self.refresh_backtest_button = QPushButton("REFRESH BACKTEST")
        self.refresh_backtest_button.clicked.connect(self.refresh_backtesting_tab)
        controls_layout.addWidget(self.refresh_backtest_button)

        self.clear_backtest_button = QPushButton("CLEAR DATA")
        self.clear_backtest_button.clicked.connect(self.clear_backtesting_data)
        self.clear_backtest_button.setStyleSheet(f"color:{T['bear']};")
        controls_layout.addWidget(self.clear_backtest_button)

        self.backtest_status_label = QLabel("Ready")
        self.backtest_status_label.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        controls_layout.addWidget(self.backtest_status_label)
        controls_layout.addStretch()
        layout.addWidget(controls_row)

        self.backtest_table = QTableWidget(0, 10)
        self.backtest_table.setHorizontalHeaderLabels(
            [
                "Symbol",
                "Pattern",
                "Trades",
                "Win Rate",
                "Expectancy",
                "Net Return",
                "Avg Bars",
                "Wins",
                "Losses",
                "Streak",
            ]
        )
        self.backtest_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.backtest_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.backtest_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.backtest_table.verticalHeader().setVisible(False)
        self.backtest_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.backtest_table, 1)

    def _build_algo_builder_tab(self):
        """Builds the full-width top-level Algo Builder tab."""
        algo_tab = QWidget()
        algo_tab.setStyleSheet(f"background-color:{T['bg']};")
        self.main_tabs.addTab(algo_tab, "🧬 Algo Builder")

        outer = QVBoxLayout(algo_tab)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Tab header bar ──────────────────────────────────────────────────
        tab_header = QWidget()
        tab_header.setFixedHeight(56)
        tab_header.setStyleSheet(
            f"background:{T['panel']}; border-bottom:1px solid {T['border']};"
        )
        thlay = QHBoxLayout(tab_header)
        thlay.setContentsMargins(20, 0, 20, 0)
        th_title = QLabel("🧬  Text-to-Algo Engine")
        th_title.setStyleSheet(
            f"color:{T['text_bright']}; font-size:18px; font-weight:bold;"
        )
        thlay.addWidget(th_title)
        thlay.addStretch()
        th_sub = QLabel(
            "Describe a strategy in plain English → AI generates Python → Instant backtest"
        )
        th_sub.setStyleSheet(f"color:{T['text_dim']}; font-size:13px;")
        thlay.addWidget(th_sub)
        outer.addWidget(tab_header)

        # ── Horizontal splitter: History | Builder ───────────────────────────
        splitter = QSplitter(Qt.Horizontal)
        splitter.setStyleSheet(
            "QSplitter::handle { background-color: #1E2535; width: 2px; }"
        )
        outer.addWidget(splitter, 1)

        # ════════════════════════════════════════════════════════════════════
        # LEFT PANEL — Strategy History
        # ════════════════════════════════════════════════════════════════════
        history_panel = QWidget()
        history_panel.setMinimumWidth(280)
        history_panel.setMaximumWidth(380)
        history_panel.setStyleSheet(
            f"background:{T['panel']}; border-right:1px solid {T['border']};"
        )
        hp_layout = QVBoxLayout(history_panel)
        hp_layout.setContentsMargins(12, 14, 12, 12)
        hp_layout.setSpacing(8)
        splitter.addWidget(history_panel)

        # History header row
        hist_header_row = QHBoxLayout()
        hist_title = QLabel("📚  Strategy History")
        hist_title.setStyleSheet(
            f"color:{T['text_bright']}; font-size:14px; font-weight:bold;"
        )
        hist_header_row.addWidget(hist_title)
        hist_header_row.addStretch()
        self.clear_history_btn = QPushButton("CLEAR ALL")
        self.clear_history_btn.setStyleSheet(
            f"color:{T['bear']}; background:transparent; border:none; font-size:12px;"
        )
        self.clear_history_btn.clicked.connect(self.clear_algo_history)
        hist_header_row.addWidget(self.clear_history_btn)
        hp_layout.addLayout(hist_header_row)

        self.history_count_lbl = QLabel("0 strategies saved")
        self.history_count_lbl.setStyleSheet(
            f"color:{T['text_dim']}; font-size:11px;"
        )
        hp_layout.addWidget(self.history_count_lbl)

        # Scrollable history list
        self.history_scroll = QScrollArea()
        self.history_scroll.setWidgetResizable(True)
        self.history_scroll.setStyleSheet(
            "border: none; background-color: transparent;"
        )
        self.history_container = QWidget()
        self.history_list_layout = QVBoxLayout(self.history_container)
        self.history_list_layout.setContentsMargins(0, 0, 0, 0)
        self.history_list_layout.setSpacing(6)
        self.history_list_layout.addStretch()
        self.history_scroll.setWidget(self.history_container)
        hp_layout.addWidget(self.history_scroll, 1)

        # ════════════════════════════════════════════════════════════════════
        # RIGHT PANEL — Builder
        # ════════════════════════════════════════════════════════════════════
        builder_panel = QWidget()
        builder_panel.setStyleSheet(f"background:{T['bg']};")
        bp_layout = QVBoxLayout(builder_panel)
        bp_layout.setContentsMargins(20, 16, 20, 16)
        bp_layout.setSpacing(10)
        splitter.addWidget(builder_panel)
        splitter.setSizes([320, 900])

        # ── Controls row (symbol + model + button) ────────────────────────
        controls_row = QWidget()
        controls_layout = QHBoxLayout(controls_row)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(10)

        sym_lbl = QLabel("Symbol")
        sym_lbl.setStyleSheet(f"color:{T['text_dim']}; font-size:12px;")
        controls_layout.addWidget(sym_lbl)

        self.algo_symbol_combo = QComboBox()
        self.algo_symbol_combo.addItems(available_symbols())
        default_idx = self.algo_symbol_combo.findText("RELIANCE")
        if default_idx >= 0:
            self.algo_symbol_combo.setCurrentIndex(default_idx)
        self.algo_symbol_combo.setFixedWidth(140)
        controls_layout.addWidget(self.algo_symbol_combo)

        model_lbl = QLabel("Model")
        model_lbl.setStyleSheet(f"color:{T['text_dim']}; font-size:12px;")
        controls_layout.addWidget(model_lbl)

        self.algo_model_combo = QComboBox()
        self.algo_model_combo.addItems([
            "gemini-2.5-pro",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
            "gemini-3-flash-preview",
            "gemini-2.5-flash",
        ])
        self.algo_model_combo.setFixedWidth(230)
        controls_layout.addWidget(self.algo_model_combo)

        controls_layout.addStretch()

        self.compile_btn = QPushButton("⚡  COMPILE & TEST")
        self.compile_btn.setFixedHeight(36)
        self.compile_btn.setStyleSheet(
            f"background:{T['accent']}; color:#FFFFFF; font-weight:bold; "
            f"border:none; border-radius:4px; padding:0 20px; font-size:13px;"
        )
        self.compile_btn.clicked.connect(self.run_algo_builder)
        controls_layout.addWidget(self.compile_btn)

        self.algo_status_label = QLabel("Ready")
        self.algo_status_label.setStyleSheet(
            f"color:{T['text_dim']}; font-size:12px; min-width:160px;"
        )
        controls_layout.addWidget(self.algo_status_label)
        bp_layout.addWidget(controls_row)

        # ── Strategy prompt input ────────────────────────────────────────
        prompt_lbl = QLabel("Strategy Description")
        prompt_lbl.setStyleSheet(
            f"color:{T['text_dim']}; font-size:12px; font-weight:bold;"
        )
        bp_layout.addWidget(prompt_lbl)

        self.algo_prompt_input = QTextEdit()
        self.algo_prompt_input.setFixedHeight(90)
        self.algo_prompt_input.setPlaceholderText(
            "e.g. Buy when the 9-EMA crosses above the 21-EMA and RSI(14) > 55. "
            "Use a 1.5× ATR stop-loss and 3× ATR take-profit. "
            "Exit long when 9-EMA crosses below 21-EMA."
        )
        self.algo_prompt_input.setStyleSheet(
            f"background:{T['surface']}; border:1px solid {T['accent']}; "
            f"color:{T['text']}; padding:8px; font-size:13px; border-radius:4px;"
        )
        bp_layout.addWidget(self.algo_prompt_input)

        # ── Code + Results in a nested vertical splitter ─────────────────
        v_splitter = QSplitter(Qt.Vertical)
        v_splitter.setStyleSheet(
            "QSplitter::handle { background-color: #1E2535; height: 2px; }"
        )
        bp_layout.addWidget(v_splitter, 1)

        # Generated Code block  (EDITABLE — edit and re-run)
        code_wrapper = QWidget()
        cw_lay = QVBoxLayout(code_wrapper)
        cw_lay.setContentsMargins(0, 0, 0, 0)
        cw_lay.setSpacing(6)

        code_header_row = QHBoxLayout()
        code_lbl = QLabel("Generated Code  ✏️  (editable)")
        code_lbl.setStyleSheet(
            f"color:{T['text_dim']}; font-size:14px; font-weight:bold;"
        )
        code_header_row.addWidget(code_lbl)
        code_header_row.addStretch()

        self.rerun_btn = QPushButton("▶  Re-Run Edited Code")
        self.rerun_btn.setFixedHeight(32)
        self.rerun_btn.setStyleSheet(
            f"background:{T['surface2']}; color:{T['bull']}; font-weight:bold; "
            f"border:1px solid {T['bull']}; border-radius:4px; "
            f"padding:0 14px; font-size:13px;"
        )
        self.rerun_btn.setToolTip("Run the code currently in the editor against the selected symbol")
        self.rerun_btn.clicked.connect(self.rerun_edited_code)
        code_header_row.addWidget(self.rerun_btn)
        cw_lay.addLayout(code_header_row)

        self.algo_code_display = QTextEdit()
        self.algo_code_display.setStyleSheet(
            f"background:{T['surface2']}; border:1px solid {T['accent']}; "
            f"color:#A8D8A8; font-family:'Consolas','Courier New',monospace; "
            f"font-size:14px; padding:10px; border-radius:4px;"
        )
        self.algo_code_display.setPlaceholderText(
            "Generated Python class will appear here — you can edit it before re-running."
        )
        cw_lay.addWidget(self.algo_code_display, 1)
        v_splitter.addWidget(code_wrapper)

        # Results block
        res_wrapper = QWidget()
        rw_lay = QVBoxLayout(res_wrapper)
        rw_lay.setContentsMargins(0, 0, 0, 0)
        rw_lay.setSpacing(4)
        res_lbl = QLabel("Backtest Results")
        res_lbl.setStyleSheet(
            f"color:{T['text_dim']}; font-size:14px; font-weight:bold;"
        )
        rw_lay.addWidget(res_lbl)
        self.algo_results_display = QTextEdit()
        self.algo_results_display.setReadOnly(True)
        self.algo_results_display.setStyleSheet(
            f"background:{T['surface']}; border:1px solid {T['border']}; "
            f"color:{T['text']}; font-family:'Consolas','Courier New',monospace; "
            f"font-size:15px; padding:12px; border-radius:4px;"
        )
        self.algo_results_display.setPlaceholderText(
            "Backtest statistics will appear here after running..."
        )
        rw_lay.addWidget(self.algo_results_display, 1)
        v_splitter.addWidget(res_wrapper)
        v_splitter.setSizes([370, 250])

        # Load existing history on startup
        self._reload_history_panel()

    def _populate_symbol_controls(self):
        for symbol in self.available_symbols:
            item = QListWidgetItem(symbol)
            self.symbol_list.addItem(item)
            if symbol in self.selected_symbols:
                item.setSelected(True)
        self._sync_focus_options()

    def _selected_from_list(self) -> list[str]:
        symbols = [item.text() for item in self.symbol_list.selectedItems()]
        return symbols or [self.available_symbols[0]]

    def _sync_focus_options(self):
        selected = self._selected_from_list()
        current_focus = self.focus_combo.currentText() or self.focus_symbol
        self.focus_combo.blockSignals(True)
        self.focus_combo.clear()
        self.focus_combo.addItems(selected)
        if current_focus in selected:
            self.focus_combo.setCurrentText(current_focus)
        else:
            self.focus_combo.setCurrentIndex(0)
        self.focus_combo.blockSignals(False)
        self.selection_status.setText(f"{len(selected)} selected")

    def apply_symbol_selection(self):
        selected = self._selected_from_list()
        focus_symbol = self.focus_combo.currentText() or selected[0]
        payload = save_selection(selected, focus_symbol)
        self.selected_symbols = payload["selected_symbols"]
        self.focus_symbol = payload["focus_symbol"]

        self.chart_symbol_label.setText(self.focus_symbol)
        self.setWindowTitle(f"Kinetic Monolith - {self.focus_symbol}")
        self.title_label.setText(f"Agent Scanner - {', '.join(self.selected_symbols[:4])}")
        if len(self.selected_symbols) > 4:
            self.title_label.setText(self.title_label.text() + " ...")
        self.status_label.setText(f"Scanning {len(self.selected_symbols)} symbol(s)")
        self.load_historical_base()
        self.refresh_backtesting_tab()

    def start_test_run(self):
        if self.stream_started:
            return

        self.apply_symbol_selection()
        with open(START_SIGNAL_FILE, "w", encoding="utf-8") as signal_file:
            signal_file.write("start\n")

        self.stream_started = True
        self.start_button.setText("TEST RUN ACTIVE")
        self.start_button.setEnabled(False)
        self.status_label.setText(f"Live replay running across {len(self.selected_symbols)} symbol(s)")

    def _extract_time(self, row, fallback_index):
        for key in ("timestamp", "time", "datetime", "date", "ts"):
            if key in row and pd.notna(row[key]):
                ts = pd.to_datetime(row[key], errors="coerce")
                if pd.notna(ts):
                    return ts
        return pd.Timestamp("2000-01-01") + pd.Timedelta(minutes=int(fallback_index))

    def load_historical_base(self):
        self.candle_data = {}
        try:
            df_base = pd.read_parquet("historical_base.parquet")
            for symbol in self.selected_symbols:
                df_target = df_base[df_base["symbol"] == symbol].copy()
                symbol_candles = []
                for idx, row in df_target.iterrows():
                    candle_time = self._extract_time(row, idx)
                    symbol_candles.append(
                        {
                            "time": candle_time,
                            "open": float(row["open"]),
                            "high": float(row["high"]),
                            "low": float(row["low"]),
                            "close": float(row["close"]),
                            "volume": float(row["volume"]),
                        }
                    )
                self.candle_data[symbol] = symbol_candles
            
            self.redraw_charts()
            focus_count = len(self.candle_data.get(self.focus_symbol, []))
            self.chart_info_label.setText(
                f"{focus_count} historical candles for {self.focus_symbol} | {len(self.selected_symbols)} symbols total"
            )
        except Exception as exc:
            self.chart_info_label.setText(f"Historical load failed: {exc}")

    def refresh_backtesting_tab(self):
        self.backtest_status_label.setText("Running backtests...")
        self.backtest_table.setRowCount(0)

        rows = []
        total_trades = 0
        weighted_expectancy = 0.0
        best_row = None

        for symbol in self.selected_symbols:
            for pattern in self.active_patterns:
                try:
                    stats = run_historical_backtest(symbol, pattern)
                except Exception as exc:
                    self.backtest_status_label.setText(f"Backtest failed for {symbol}: {exc}")
                    continue

                total_trades += stats["total_trades"]
                weighted_expectancy += stats["expectancy_pct"] * stats["total_trades"]
                row = {
                    "symbol": symbol,
                    "pattern": pattern.name,
                    **stats,
                }
                rows.append(row)

                if best_row is None or row["win_rate"] > best_row["win_rate"]:
                    best_row = row

        rows.sort(key=lambda item: (item["total_trades"], item["win_rate"]), reverse=True)
        self.backtest_table.setRowCount(len(rows))

        for row_index, row in enumerate(rows):
            streak_text = f"W{row['max_win_streak']} / L{row['max_loss_streak']}"
            values = [
                row["symbol"],
                row["pattern"],
                str(row["total_trades"]),
                f"{row['win_rate']:.2%}",
                f"{row['expectancy_pct']:.2%}",
                f"{row['net_return_pct']:.2%}",
                f"{row['avg_bars_to_exit']:.1f}",
                str(row["wins"]),
                str(row["losses"]),
                streak_text,
            ]
            for column_index, value in enumerate(values):
                item = QTableWidgetItem(value)
                item.setTextAlignment(Qt.AlignCenter)
                self.backtest_table.setItem(row_index, column_index, item)

        self.backtest_table.resizeColumnsToContents()

        if not rows:
            self.backtest_summary_label.setText(
                "No backtest rows were produced. Check that the selected symbols have parquet history."
            )
            self.backtest_status_label.setText("No backtest data")
            return

        aggregate_expectancy = (weighted_expectancy / total_trades) if total_trades else 0.0
        best_text = (
            f"Best win rate: {best_row['symbol']} {best_row['pattern']} at {best_row['win_rate']:.2%}"
            if best_row
            else "Best win rate unavailable"
        )
        self.backtest_summary_label.setText(
            f"Selected symbols: {len(self.selected_symbols)} | Total simulated trades: {total_trades} | "
            f"Average expectancy per trade: {aggregate_expectancy:.2%} | {best_text}"
        )
        self.backtest_status_label.setText(f"Backtest updated for {len(rows)} symbol/pattern runs")

    def clear_backtesting_data(self):
        """Clears the backtesting table and summary labels."""
        self.backtest_table.setRowCount(0)
        self.backtest_summary_label.setText("No backtest data loaded yet.")
        self.backtest_status_label.setText("Data cleared")

    def redraw_charts(self):
        focus_candles = self.candle_data.get(self.focus_symbol, [])
        if not focus_candles:
            return
        df = pd.DataFrame(focus_candles).sort_values("time").reset_index(drop=True)
        df["ema_fast"] = df["close"].ewm(span=9, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=21, adjust=False).mean()
        self.qt_chart.set(df[["time", "open", "high", "low", "close", "volume"]])
        self.ema_fast_line.set(df[["time", "ema_fast"]].rename(columns={"ema_fast": "EMA 9"}))
        self.ema_slow_line.set(df[["time", "ema_slow"]].rename(columns={"ema_slow": "EMA 21"}))
        self.qt_chart.fit()
        self.chart_price_label.setText(f"{df.iloc[-1]['close']:.2f}")

    def update_chart(self, raw):
        symbol = raw.get("symbol")
        if not symbol:
            return

        parsed_time = pd.to_datetime(raw.get("timestamp"), errors="coerce")
        if pd.isna(parsed_time):
            parsed_time = pd.Timestamp.now()

        candle = {
            "time": parsed_time,
            "open": float(raw["open"]),
            "high": float(raw["high"]),
            "low": float(raw["low"]),
            "close": float(raw["close"]),
            "volume": float(raw["volume"]),
        }

        if symbol not in self.candle_data:
            self.candle_data[symbol] = []
        
        symbol_data = self.candle_data[symbol]
        if symbol_data and pd.to_datetime(symbol_data[-1]["time"]) == parsed_time:
            symbol_data[-1] = candle
        else:
            symbol_data.append(candle)

        if symbol == self.focus_symbol:
            bar = pd.Series(candle)
            self.qt_chart.update(bar)

            closes = pd.Series([entry["close"] for entry in symbol_data])
            ema_fast = float(closes.ewm(span=9, adjust=False).mean().iloc[-1])
            ema_slow = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])
            self.ema_fast_line.update(pd.Series({"time": parsed_time, "EMA 9": ema_fast}))
            self.ema_slow_line.update(pd.Series({"time": parsed_time, "EMA 21": ema_slow}))

            self.chart_price_label.setText(f"{candle['close']:.2f}")
            self.chart_info_label.setText(
                f"Focus {self.focus_symbol} | Engine monitoring {len(self.selected_symbols)} symbol(s)"
            )

    def check_ai_alerts(self):
        if not os.path.exists(ALERTS_FILE):
            return
        try:
            with open(ALERTS_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception:
            return

        alerts = payload if isinstance(payload, list) else [payload]
        new_alerts = []
        for alert in alerts:
            event_id = alert.get("event_id") or f"{alert.get('symbol')}:{alert.get('timestamp')}"
            if event_id in self.seen_alert_ids:
                continue
            self.seen_alert_ids.add(event_id)
            new_alerts.append(alert)

        if not new_alerts:
            return

        for alert in reversed(new_alerts):
            card = AlertCard(alert)
            card.clicked.connect(self.open_deep_dive)
            # Insert at the top (after the stretch)
            self.alerts_layout.insertWidget(0, card)

    def open_deep_dive(self, alert_data):
        """
        Triggered when an alert card is clicked. Opens the chat panel with context.
        """
        self.active_session_id = alert_data.get("session_id")
        self.chat_title.setText(f"Deep-Dive: {alert_data['symbol']} ({alert_data['agent_mode']})")
        self.chat_panel.show()
        
        # Clear and show history start
        self.chat_history.clear()
        self.chat_history.append(f"<b style='color:{T['accent']}'>[SYSTEM]</b> Follow-up session active.")
        self.chat_history.append(f"<b style='color:{T['bull']}'>[ALERT]</b> {alert_data['synthesis']}")
        
        # Sync chart if timestamp is available
        alert_time = pd.to_datetime(alert_data['timestamp'], errors='coerce')
        if not pd.isna(alert_time) and alert_data['symbol'] == self.focus_symbol:
            # Simple jump - real jump might need index-based lookup in candle_data
            self.chart_info_label.setText(f"Synced to alert at {alert_time}")

    def send_chat_query(self):
        query = self.chat_input.toPlainText().strip()
        if not query or not self.active_session_id:
            return
        
        self.chat_history.append(f"<br><b style='color:{T['accent2']}'>[YOU]</b> {query}")
        self.chat_input.clear()
        
        # Use background thread to avoid blocking UI
        self.chat_worker = DeepDiveWorker(
            query, 
            self.active_session_id, 
            self.agent_combo.currentText(), 
            self.model_combo.currentText()
        )
        self.chat_worker.response_ready.connect(self.handle_chat_response)
        self.chat_worker.start()

    def handle_chat_response(self, response):
        self.chat_history.append(f"<br><b style='color:{T['bull']}'>[AGENT]</b> {response}")
        # Scroll to bottom
        self.chat_history.ensureCursorVisible()

    def run_algo_builder(self):
        """
        Triggered by the 'COMPILE & TEST' button.
        Validates input, disables the button, then hands off to AlgoBuilderWorker.
        The UI is never blocked — all LLM + backtest work happens in the background thread.
        """
        prompt = self.algo_prompt_input.toPlainText().strip()
        if not prompt:
            self.algo_status_label.setText("⚠️ Please enter a strategy description first.")
            return

        symbol = self.algo_symbol_combo.currentText() or "RELIANCE"
        model_name = self.algo_model_combo.currentText()

        # Disable button to prevent double-clicks
        self.compile_btn.setEnabled(False)
        self.compile_btn.setText("⏳  Working...")
        self.algo_status_label.setText("🤖 Starting Algo-Architect...")
        self.algo_code_display.clear()
        self.algo_results_display.clear()

        self.algo_worker = AlgoBuilderWorker(prompt, symbol, model_name)
        self.algo_worker.status_update.connect(self.algo_status_label.setText)
        self.algo_worker.code_ready.connect(self.on_algo_code_ready)
        self.algo_worker.result_ready.connect(
            lambda stats: self.on_algo_result_ready(stats, prompt, symbol)
        )
        self.algo_worker.finished.connect(self._on_algo_worker_finished)
        self.algo_worker.start()

    def _on_algo_worker_finished(self):
        """Re-enables the compile button once the worker thread is done."""
        self.compile_btn.setEnabled(True)
        self.compile_btn.setText("⚡  COMPILE & TEST")

    def on_algo_code_ready(self, code: str):
        """Displays the AI-generated code in the code viewer panel."""
        self._last_generated_code = code
        self.algo_code_display.setPlainText(code)

    def on_algo_result_ready(self, stats: dict, prompt: str = "", symbol: str = ""):
        """
        Renders the backtest results (or error) into the results panel.
        Uses rich colour-coded HTML for a premium terminal feel.
        """
        if not stats.get("ok"):
            err = stats.get("error", "Unknown error")
            tb = stats.get("traceback", "")
            html = (
                f"<span style='color:{T['bear']}; font-weight:bold;'>❌ FAILED</span><br><br>"
                f"<span style='color:{T['warn']};'>{err}</span>"
            )
            if tb:
                tb_safe = tb.replace("\n", "<br>").replace(" ", "&nbsp;")
                html += f"<br><br><span style='color:{T['text_dim']}; font-size:10px;'>{tb_safe}</span>"
            self.algo_results_display.setHtml(html)
            return

        # Success — format stats table
        wr = stats.get("win_rate_pct", 0.0)
        ret = stats.get("return_pct", 0.0)
        total = stats.get("total_trades", 0)
        expectancy = stats.get("expectancy_pct", 0.0)
        sharpe = stats.get("sharpe_ratio", 0.0)
        max_dd = stats.get("max_drawdown_pct", 0.0)
        final_eq = stats.get("final_equity", 100_000.0)
        max_ws = stats.get("max_win_streak", 0)
        max_ls = stats.get("max_loss_streak", 0)
        sym = stats.get("symbol", "?")

        ret_color = T["bull"] if ret >= 0 else T["bear"]
        wr_color = T["bull"] if wr >= 50 else T["bear"]

        html = (
            f"<span style='color:{T['accent']}; font-weight:bold; font-size:14px;'>"
            f"✅ {sym} — Backtest Complete</span><br><br>"
            f"<table width='100%' cellspacing='4'>"
            f"<tr><td style='color:{T['text_dim']};'>Win Rate</td>"
            f"    <td style='color:{wr_color}; font-weight:bold;'>{wr:.1f}%</td></tr>"
            f"<tr><td style='color:{T['text_dim']};'>Return</td>"
            f"    <td style='color:{ret_color}; font-weight:bold;'>{ret:+.2f}%</td></tr>"
            f"<tr><td style='color:{T['text_dim']};'>Total Trades</td>"
            f"    <td style='color:{T['text']};'>{total}</td></tr>"
            f"<tr><td style='color:{T['text_dim']};'>Expectancy</td>"
            f"    <td style='color:{T['text']};'>{expectancy:.2f}%</td></tr>"
            f"<tr><td style='color:{T['text_dim']};'>Sharpe Ratio</td>"
            f"    <td style='color:{T['text']};'>{sharpe:.3f}</td></tr>"
            f"<tr><td style='color:{T['text_dim']};'>Max Drawdown</td>"
            f"    <td style='color:{T['bear']};'>{max_dd:.2f}%</td></tr>"
            f"<tr><td style='color:{T['text_dim']};'>Final Equity</td>"
            f"    <td style='color:{T['text_bright']};'>₹{final_eq:,.0f}</td></tr>"
            f"<tr><td style='color:{T['text_dim']};'>Win Streak</td>"
            f"    <td style='color:{T['bull']};'>W{max_ws} / L{max_ls}</td></tr>"
            f"</table>"
        )
        self.algo_results_display.setHtml(html)
        # Save to history regardless of success
        self._save_to_history(prompt, symbol, getattr(self, "_last_generated_code", ""), stats)

    # ── History persistence ──────────────────────────────────────────────

    def _reload_history_panel(self):
        """Clears and rebuilds the history card list from algo_history.json."""
        # Clear existing cards (keep stretch at index 0)
        while self.history_list_layout.count() > 1:
            child = self.history_list_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

        entries = self._load_history_file()
        count = len(entries)
        self.history_count_lbl.setText(
            f"{count} strateg{'y' if count == 1 else 'ies'} saved"
        )

        # Insert newest first
        for entry in reversed(entries):
            card = StrategyHistoryCard(entry)
            card.clicked.connect(self._load_history_entry)
            self.history_list_layout.insertWidget(0, card)

    def _load_history_file(self) -> list[dict]:
        """Reads algo_history.json, returns [] on any error."""
        if not os.path.exists(ALGO_HISTORY_FILE):
            return []
        try:
            with open(ALGO_HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def _save_to_history(self, prompt: str, symbol: str, code: str, stats: dict):
        """Appends the current run to algo_history.json and refreshes the panel."""
        if not prompt:
            return
        entries = self._load_history_file()
        entry = {
            "id": str(uuid.uuid4()),
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "prompt": prompt,
            "symbol": symbol,
            "model": self.algo_model_combo.currentText(),
            "code": code,
            "stats": stats,
        }
        entries.append(entry)
        try:
            with open(ALGO_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(entries, f, indent=2)
        except Exception as exc:
            print(f"[HISTORY SAVE ERROR] {exc}")
        self._reload_history_panel()

    def _load_history_entry(self, entry: dict):
        """Populates the builder form with a previously saved strategy."""
        self.algo_prompt_input.setPlainText(entry.get("prompt", ""))
        sym = entry.get("symbol", "RELIANCE")
        idx = self.algo_symbol_combo.findText(sym)
        if idx >= 0:
            self.algo_symbol_combo.setCurrentIndex(idx)
        code = entry.get("code", "")
        if code:
            self.algo_code_display.setPlainText(code)
        stats = entry.get("stats", {})
        if stats:
            self.on_algo_result_ready(
                stats,
                entry.get("prompt", ""),
                sym,
            )
        self.algo_status_label.setText(
            f"Loaded from history — {entry.get('timestamp', '')[:16]}"
        )

    def clear_algo_history(self):
        """Wipes algo_history.json and refreshes the history panel."""
        try:
            with open(ALGO_HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)
        except Exception:
            pass
        self._reload_history_panel()
        self.algo_status_label.setText("Strategy history cleared.")

    def clear_alert_history(self):
        while self.alerts_layout.count() > 1: # Keep the stretch
            child = self.alerts_layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
        
        self.seen_alert_ids.clear()
        try:
            with open(ALERTS_FILE, "w", encoding="utf-8") as f:
                json.dump([], f, indent=2)
        except Exception:
            pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    terminal = TradingTerminal()
    terminal.refresh_backtesting_tab()
    terminal.show()
    sys.exit(app.exec_())
