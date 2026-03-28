from __future__ import annotations

import json
import os
import sys

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
    QVBoxLayout,
    QWidget,
)

from backtester import run_historical_backtest
from patterns import BullishEngulfing, VolumeBreakout
from runtime_config import available_symbols, load_selection, save_selection

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

STYLESHEET = f"""
QMainWindow {{ background-color: {T['bg']}; }}
QWidget {{
    background-color: transparent;
    color: {T['text']};
    font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace;
    font-size: 11px;
}}
QPushButton {{
    background-color: {T['surface']};
    color: "#FFFFFF";
    border: 1px solid {T['border']};
    border-radius: 3px;
    padding: 5px 10px;
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
}}
QTabWidget::pane {{
    border: 1px solid {T['border']};
    background: {T['bg']};
}}
QTabBar::tab {{
    background: {T['surface']};
    color: {T['text_dim']};
    border: 1px solid {T['border']};
    padding: 7px 14px;
    min-width: 120px;
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
}}
QHeaderView::section {{
    background-color: {T['panel']};
    color: {T['text_bright']};
    border: 1px solid {T['border']};
    padding: 4px;
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


class TradingTerminal(QMainWindow):
    def __init__(self):
        super().__init__()

        if QtChart is None:
            raise RuntimeError(f"lightweight_charts QtChart failed to import: {QTCHART_IMPORT_ERROR}")

        selection = load_selection()
        self.available_symbols = available_symbols()
        self.selected_symbols = selection["selected_symbols"]
        self.focus_symbol = selection["focus_symbol"]

        self.candle_data = []
        self.seen_alert_ids = set()
        self.stream_started = False
        self.active_patterns = [VolumeBreakout(), BullishEngulfing()]

        self.setWindowTitle(f"Kinetic Monolith - {self.focus_symbol}")
        self.showMaximized()
        self.setStyleSheet(STYLESHEET)

        self._build_ui()
        self._populate_symbol_controls()
        self.load_historical_base()

        self.kafka_thread = KafkaWorker()
        self.kafka_thread.new_candle_signal.connect(self.update_chart)
        self.kafka_thread.start()

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
        header.setFixedHeight(52)
        header.setStyleSheet(f"background-color:{T['panel']}; border-bottom:1px solid {T['border']};")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(14, 0, 14, 0)

        self.title_label = QLabel("Agent Scanner")
        self.title_label.setStyleSheet(
            f"color:{T['text_bright']}; font-size:13px; font-weight:bold; letter-spacing:1px;"
        )
        header_layout.addWidget(self.title_label)

        self.status_label = QLabel("Idle")
        self.status_label.setStyleSheet(f"color:{T['text_dim']}; font-size:10px;")
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
        chart_header.setFixedHeight(40)
        chart_header.setStyleSheet(f"background-color:{T['panel']}; border-bottom:1px solid {T['border']};")
        chart_header_layout = QHBoxLayout(chart_header)
        chart_header_layout.setContentsMargins(12, 0, 12, 0)

        self.chart_symbol_label = QLabel(self.focus_symbol)
        self.chart_symbol_label.setStyleSheet(f"color:{T['text_bright']}; font-size:12px; font-weight:bold;")
        chart_header_layout.addWidget(self.chart_symbol_label)

        self.chart_price_label = QLabel("--")
        self.chart_price_label.setStyleSheet(f"color:{T['accent']}; font-size:12px; font-weight:bold;")
        chart_header_layout.addWidget(self.chart_price_label)
        chart_header_layout.addStretch()

        self.chart_info_label = QLabel("Waiting for stream")
        self.chart_info_label.setStyleSheet(f"color:{T['text_dim']}; font-size:10px;")
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
            font_size=11,
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
            name="EMA 9", color=T["ema_fast"], width=1, price_line=False, price_label=False
        )
        self.ema_slow_line = self.qt_chart.create_line(
            name="EMA 21", color=T["ema_slow"], width=1, price_line=False, price_label=False
        )
        chart_host_layout.addWidget(self.qt_chart.get_webview())
        chart_layout.addWidget(self.chart_host, 1)

        right_panel = QWidget()
        right_panel.setFixedWidth(360)
        right_panel.setStyleSheet(f"background-color:{T['panel']}; border-left:1px solid {T['border']};")
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(12, 12, 12, 12)
        right_layout.setSpacing(10)
        middle_layout.addWidget(right_panel)

        scanner_label = QLabel("Scanner Controls")
        scanner_label.setStyleSheet(f"color:{T['text_bright']}; font-size:11px; font-weight:bold;")
        right_layout.addWidget(scanner_label)

        scanner_hint = QLabel("Select any symbols to scan in parallel. Focus controls the main chart.")
        scanner_hint.setWordWrap(True)
        scanner_hint.setStyleSheet(f"color:{T['text_dim']}; font-size:10px;")
        right_layout.addWidget(scanner_hint)

        self.symbol_list = QListWidget()
        self.symbol_list.setSelectionMode(QListWidget.MultiSelection)
        self.symbol_list.itemSelectionChanged.connect(self._sync_focus_options)
        right_layout.addWidget(self.symbol_list)

        focus_row = QWidget()
        focus_layout = QHBoxLayout(focus_row)
        focus_layout.setContentsMargins(0, 0, 0, 0)
        focus_layout.setSpacing(6)
        focus_label = QLabel("Focus")
        focus_label.setStyleSheet(f"color:{T['text_dim']}; font-size:10px;")
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
        self.selection_status.setStyleSheet(f"color:{T['text_dim']}; font-size:10px;")
        button_layout.addWidget(self.selection_status)
        button_layout.addStretch()
        right_layout.addWidget(button_row)

        alerts_header = QWidget()
        alerts_header_layout = QHBoxLayout(alerts_header)
        alerts_header_layout.setContentsMargins(0, 0, 0, 0)
        alerts_header_layout.setSpacing(6)

        alerts_label = QLabel("Swarm Alerts")
        alerts_label.setStyleSheet(f"color:{T['text_bright']}; font-size:11px; font-weight:bold;")
        alerts_header_layout.addWidget(alerts_label)
        alerts_header_layout.addStretch()

        self.clear_alerts_button = QPushButton("CLEAR")
        self.clear_alerts_button.clicked.connect(self.clear_alert_history)
        alerts_header_layout.addWidget(self.clear_alerts_button)
        right_layout.addWidget(alerts_header)

        self.alerts_area = QTextEdit()
        self.alerts_area.setReadOnly(True)
        self.alerts_area.setStyleSheet(
            f"background-color:{T['surface']}; border:1px solid {T['border']}; color:{T['text']}; padding:8px;"
        )
        right_layout.addWidget(self.alerts_area, 1)

        self._build_backtesting_tab()

    def _build_backtesting_tab(self):
        backtest_tab = QWidget()
        backtest_tab.setStyleSheet(f"background-color:{T['bg']};")
        self.main_tabs.addTab(backtest_tab, "Backtesting")

        layout = QVBoxLayout(backtest_tab)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        heading = QLabel("Historical Backtest Metrics")
        heading.setStyleSheet(f"color:{T['text_bright']}; font-size:14px; font-weight:bold;")
        layout.addWidget(heading)

        subtitle = QLabel(
            "This uses the same pattern logic as the scanner. A trade is counted when a signal hits, "
            "then either the 2% target or 1% stop is reached later in the historical data."
        )
        subtitle.setWordWrap(True)
        subtitle.setStyleSheet(f"color:{T['text_dim']}; font-size:10px;")
        layout.addWidget(subtitle)

        summary_frame = QFrame()
        summary_frame.setStyleSheet(
            f"background-color:{T['panel']}; border:1px solid {T['border']}; border-radius:4px;"
        )
        summary_layout = QVBoxLayout(summary_frame)
        summary_layout.setContentsMargins(12, 12, 12, 12)
        summary_layout.setSpacing(6)

        self.backtest_summary_label = QLabel("No backtest data loaded yet.")
        self.backtest_summary_label.setWordWrap(True)
        self.backtest_summary_label.setStyleSheet(f"color:{T['text']}; font-size:11px;")
        summary_layout.addWidget(self.backtest_summary_label)

        metrics_hint = QLabel(
            "Key metrics: win rate, total trades, expectancy per trade, average bars to exit, and streaks."
        )
        metrics_hint.setWordWrap(True)
        metrics_hint.setStyleSheet(f"color:{T['text_dim']}; font-size:10px;")
        summary_layout.addWidget(metrics_hint)
        layout.addWidget(summary_frame)

        controls_row = QWidget()
        controls_layout = QHBoxLayout(controls_row)
        controls_layout.setContentsMargins(0, 0, 0, 0)
        controls_layout.setSpacing(8)

        self.refresh_backtest_button = QPushButton("REFRESH BACKTEST")
        self.refresh_backtest_button.clicked.connect(self.refresh_backtesting_tab)
        controls_layout.addWidget(self.refresh_backtest_button)

        self.backtest_status_label = QLabel("Ready")
        self.backtest_status_label.setStyleSheet(f"color:{T['text_dim']}; font-size:10px;")
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

    def _populate_symbol_controls(self):
        self.symbol_list.clear()
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
        self.candle_data = []
        try:
            df = pd.read_parquet("historical_base.parquet")
            df_target = df[df["symbol"] == self.focus_symbol].copy()
            for idx, row in df_target.iterrows():
                candle_time = self._extract_time(row, idx)
                self.candle_data.append(
                    {
                        "time": candle_time,
                        "open": float(row["open"]),
                        "high": float(row["high"]),
                        "low": float(row["low"]),
                        "close": float(row["close"]),
                        "volume": float(row["volume"]),
                    }
                )
            self.redraw_charts()
            self.chart_info_label.setText(
                f"{len(self.candle_data)} historical candles loaded for {self.focus_symbol}"
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

    def redraw_charts(self):
        if not self.candle_data:
            return
        df = pd.DataFrame(self.candle_data).sort_values("time").reset_index(drop=True)
        df["ema_fast"] = df["close"].ewm(span=9, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=21, adjust=False).mean()
        self.qt_chart.set(df[["time", "open", "high", "low", "close", "volume"]])
        self.ema_fast_line.set(df[["time", "ema_fast"]].rename(columns={"ema_fast": "EMA 9"}))
        self.ema_slow_line.set(df[["time", "ema_slow"]].rename(columns={"ema_slow": "EMA 21"}))
        self.qt_chart.fit()
        self.chart_price_label.setText(f"{df.iloc[-1]['close']:.2f}")

    def update_chart(self, raw):
        if raw.get("symbol") != self.focus_symbol:
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

        if self.candle_data and pd.to_datetime(self.candle_data[-1]["time"]) == parsed_time:
            self.candle_data[-1] = candle
        else:
            self.candle_data.append(candle)

        bar = pd.Series(candle)
        self.qt_chart.update(bar)

        closes = pd.Series([entry["close"] for entry in self.candle_data])
        ema_fast = float(closes.ewm(span=9, adjust=False).mean().iloc[-1])
        ema_slow = float(closes.ewm(span=21, adjust=False).mean().iloc[-1])
        self.ema_fast_line.update(pd.Series({"time": parsed_time, "EMA 9": ema_fast}))
        self.ema_slow_line.update(pd.Series({"time": parsed_time, "EMA 21": ema_slow}))

        self.chart_price_label.setText(f"{candle['close']:.2f}")
        self.chart_info_label.setText(
            f"Focus {self.focus_symbol} | open scanner on {len(self.selected_symbols)} symbol(s)"
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

        current_html = self.alerts_area.toHtml()
        blocks = []
        for alert in reversed(new_alerts):
            blocks.append(
                f"""
                <div style="margin:0 0 12px 0;padding:10px 12px;
                    background:{T['surface']};border-left:3px solid {T['accent']};border-radius:0 3px 3px 0;">
                  <div style="color:{T['accent']};font-size:10px;font-weight:bold;letter-spacing:1px;margin-bottom:6px;">
                    {alert.get('symbol', '')} [{alert.get('pattern', 'ALERT')}]
                    <span style="color:{T['text_dim']};font-weight:normal;">[{alert.get('timestamp','')}]</span>
                  </div>
                  <div style="margin-bottom:5px;"><span style="color:{T['bull']};font-weight:bold;">[PATTERN]</span>
                    <span style="color:{T['text']};"> {alert.get('quant','')}</span>
                  </div>
                  <div style="margin-bottom:5px;"><span style="color:{T['ema_fast']};font-weight:bold;">[RISK]</span>
                    <span style="color:{T['text']};"> {alert.get('risk','')}</span>
                  </div>
                  <div style="margin-top:8px;padding-top:8px;border-top:1px solid {T['border']};
                      color:{T['text_bright']};font-weight:bold;">
                    {alert.get('synthesis','')}
                  </div>
                </div>
                """
            )
        self.alerts_area.setHtml("".join(blocks) + current_html)

    def clear_alert_history(self):
        self.alerts_area.clear()
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
