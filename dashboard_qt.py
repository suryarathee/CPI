import json
import os
import sys

import numpy as np
import pandas as pd
from confluent_kafka import Consumer
from PyQt5.QtCore import QThread, QTimer, Qt, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QSplitter,
    QStatusBar,
    QTextEdit,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

try:
    from lightweight_charts.widgets import QtChart
except Exception as exc:  # pragma: no cover
    QtChart = None
    QTCHART_IMPORT_ERROR = exc
else:
    QTCHART_IMPORT_ERROR = None


KAFKA_BROKER = "localhost:9092"
TOPIC = "nse_live_candles"
TARGET_STOCK = "RELIANCE"
ALERTS_FILE = "alerts.json"

T = {
    "bg": "#0B0E11",
    "panel": "#131722",
    "surface": "#1C2030",
    "border": "#2A2E39",
    "border_hi": "#3D4663",
    "text": "#D1D4DC",
    "text_dim": "#6B7280",
    "text_bright": "#FFFFFF",
    "accent": "#2962FF",
    "accent_glow": "#1E53E5",
    "bull": "#26A69A",
    "bear": "#EF5350",
    "vol_bull": "#1B6157",
    "vol_bear": "#7D2B2B",
    "ema_fast": "#F7C948",
    "ema_slow": "#A78BFA",
    "crosshair": "#4A5568",
    "toolbar_bg": "#161A25",
    "status_bg": "#0F1219",
}

STYLESHEET = f"""
QMainWindow {{
    background-color: {T['bg']};
}}
QWidget {{
    background-color: transparent;
    color: {T['text']};
    font-family: 'JetBrains Mono', 'Consolas', 'Courier New', monospace;
}}
QSplitter::handle {{
    background-color: {T['border']};
}}
QSplitter::handle:horizontal {{
    width: 2px;
}}
QSplitter::handle:vertical {{
    height: 2px;
}}
QTextEdit {{
    background-color: {T['surface']};
    border: 1px solid {T['border']};
    border-radius: 4px;
    color: {T['text']};
    selection-background-color: {T['accent']};
    padding: 6px;
}}
QToolBar {{
    background-color: {T['toolbar_bg']};
    border-bottom: 1px solid {T['border']};
    spacing: 4px;
    padding: 4px 8px;
}}
QStatusBar {{
    background-color: {T['status_bg']};
    border-top: 1px solid {T['border']};
    color: {T['text_dim']};
    font-size: 11px;
}}
QPushButton {{
    background-color: {T['surface']};
    color: {T['text']};
    border: 1px solid {T['border']};
    border-radius: 3px;
    padding: 4px 10px;
    font-size: 11px;
}}
QPushButton:hover {{
    background-color: {T['border']};
    border-color: {T['border_hi']};
    color: {T['text_bright']};
}}
QPushButton#accentBtn {{
    background-color: {T['accent']};
    border-color: {T['accent']};
    color: white;
    font-weight: bold;
}}
QPushButton#accentBtn:hover {{
    background-color: {T['accent_glow']};
}}
QComboBox {{
    background-color: {T['surface']};
    color: {T['text']};
    border: 1px solid {T['border']};
    border-radius: 3px;
    padding: 3px 8px;
    font-size: 11px;
}}
QComboBox::drop-down {{ border: none; }}
QComboBox QAbstractItemView {{
    background-color: {T['surface']};
    border: 1px solid {T['border_hi']};
    selection-background-color: {T['accent']};
    color: {T['text']};
}}
"""


class PanelFrame(QFrame):
    def __init__(self, title="", parent=None):
        super().__init__(parent)
        self.setStyleSheet(
            f"""
            PanelFrame {{
                background-color: {T['panel']};
                border: 1px solid {T['border']};
                border-radius: 4px;
            }}
        """
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        if title:
            title_bar = QWidget()
            title_bar.setFixedHeight(28)
            title_bar.setStyleSheet(
                f"""
                background-color: {T['toolbar_bg']};
                border-bottom: 1px solid {T['border']};
                border-radius: 0px;
            """
            )
            tb_layout = QHBoxLayout(title_bar)
            tb_layout.setContentsMargins(10, 0, 10, 0)

            lbl = QLabel(title.upper())
            lbl.setStyleSheet(
                f"""
                color: {T['text_dim']};
                font-size: 10px;
                letter-spacing: 1.8px;
                font-weight: bold;
                background: transparent;
                border: none;
            """
            )
            tb_layout.addWidget(lbl)
            tb_layout.addStretch()
            layout.addWidget(title_bar)

        self.content = QWidget()
        self.content.setStyleSheet("background: transparent; border: none;")
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(0)
        layout.addWidget(self.content)


class KafkaWorker(QThread):
    new_candle_signal = pyqtSignal(dict)

    def run(self):
        consumer = Consumer(
            {
                "bootstrap.servers": KAFKA_BROKER,
                "group.id": "pyqt-terminal-v3",
                "auto.offset.reset": "latest",
            }
        )
        consumer.subscribe([TOPIC])
        while True:
            msg = consumer.poll(0.5)
            if msg is None or msg.error():
                continue
            try:
                raw = json.loads(msg.value().decode("utf-8"))
                if raw.get("symbol") == TARGET_STOCK:
                    self.new_candle_signal.emit(raw)
            except Exception:
                pass


class TickerBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background: transparent;")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(16)

        self._fields = {}
        for key, label in [("O", "O"), ("H", "H"), ("L", "L"), ("C", "C"), ("V", "VOL")]:
            col = QWidget()
            cl = QVBoxLayout(col)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(0)
            lbl = QLabel(label)
            lbl.setStyleSheet(f"color: {T['text_dim']}; font-size: 9px; letter-spacing: 1px;")
            val = QLabel("-")
            val.setStyleSheet(f"color: {T['text_bright']}; font-size: 12px; font-weight: bold;")
            cl.addWidget(lbl)
            cl.addWidget(val)
            self._fields[key] = val
            layout.addWidget(col)

        self.change_lbl = QLabel("")
        self.change_lbl.setStyleSheet("font-size: 13px; font-weight: bold; padding: 2px 8px; border-radius: 3px;")
        layout.addWidget(self.change_lbl)

    def update(self, o, h, l, c, v):
        self._fields["O"].setText(f"Rs{o:.2f}")
        self._fields["H"].setText(f"Rs{h:.2f}")
        self._fields["L"].setText(f"Rs{l:.2f}")
        self._fields["C"].setText(f"Rs{c:.2f}")
        self._fields["V"].setText(f"{int(v):,}")
        pct = ((c - o) / o * 100) if o else 0.0
        sign = "+" if pct >= 0 else ""
        color = T["bull"] if pct >= 0 else T["bear"]
        bg = T["vol_bull"] if pct >= 0 else T["vol_bear"]
        self.change_lbl.setText(f"{sign}{pct:.2f}%")
        self.change_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: bold; padding: 2px 8px; "
            f"border-radius: 3px; color: {color}; background: {bg};"
        )


class TradingTerminal(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"Pattern Intelligence Terminal · {TARGET_STOCK}")
        self.showMaximized()
        self.setStyleSheet(STYLESHEET)

        if QtChart is None:
            raise RuntimeError(
                "lightweight_charts QtChart import failed. Install dependencies and retry. "
                f"Error: {QTCHART_IMPORT_ERROR}"
            )

        self._build_toolbar()
        self._build_statusbar()
        self._build_layout()

        self.candle_data = []
        self.close_prices = []
        self.volume_data = []
        self.last_alert_ts = ""
        self.ema_fast_val = None
        self.ema_slow_val = None

        self.load_historical_base()

        self.kafka_thread = KafkaWorker()
        self.kafka_thread.new_candle_signal.connect(self.update_chart)
        self.kafka_thread.start()

        self.alert_timer = QTimer()
        self.alert_timer.timeout.connect(self.check_ai_alerts)
        self.alert_timer.start(1000)

        self._blink_state = True
        self._blink_timer = QTimer()
        self._blink_timer.timeout.connect(self._blink_live)
        self._blink_timer.start(900)

        self.status_left.setText(f"  Connected · Kafka {KAFKA_BROKER}  ·  Topic: {TOPIC}")

    def _build_toolbar(self):
        tb = QToolBar("Main Toolbar", self)
        tb.setMovable(False)
        tb.setFloatable(False)
        self.addToolBar(Qt.TopToolBarArea, tb)

        sym_lbl = QLabel(f"  {TARGET_STOCK}  ")
        sym_lbl.setStyleSheet(
            f"color: {T['text_bright']}; font-size: 15px; font-weight: bold; "
            f"padding: 0 12px; border-right: 1px solid {T['border']};"
        )
        tb.addWidget(sym_lbl)

        exch_lbl = QLabel("  NSE · EQUITY  ")
        exch_lbl.setStyleSheet(
            f"color: {T['text_dim']}; font-size: 10px; letter-spacing: 1px; padding: 0 8px; "
            f"border-right: 1px solid {T['border']};"
        )
        tb.addWidget(exch_lbl)

        interval_combo = QComboBox()
        interval_combo.addItems(["1m", "3m", "5m", "15m", "30m", "1h", "1D"])
        interval_combo.setCurrentText("1m")
        interval_combo.setFixedWidth(60)
        tb.addWidget(interval_combo)

        tb.addSeparator()

        for indicator in ["EMA", "MACD", "RSI", "BB", "VWAP"]:
            btn = QPushButton(indicator)
            btn.setFixedHeight(24)
            tb.addWidget(btn)

        tb.addSeparator()

        for tool in ["Trend", "Fib", "Ruler"]:
            btn = QPushButton(tool)
            btn.setFixedHeight(24)
            tb.addWidget(btn)

        tb.addSeparator()

        self.live_badge = QPushButton("LIVE")
        self.live_badge.setObjectName("accentBtn")
        self.live_badge.setFixedHeight(24)
        tb.addWidget(self.live_badge)

        tb.addWidget(QWidget())
        self.ticker_bar = TickerBar()
        tb.addWidget(self.ticker_bar)

    def _build_statusbar(self):
        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_left = QLabel("  Connecting to Kafka...")
        self.status_center = QLabel()
        self.status_right = QLabel()
        self.status.addWidget(self.status_left, 1)
        self.status.addPermanentWidget(self.status_center)
        self.status.addPermanentWidget(self.status_right)

    def _build_layout(self):
        central = QWidget()
        central.setStyleSheet(f"background-color: {T['bg']};")
        self.setCentralWidget(central)

        root = QHBoxLayout(central)
        root.setContentsMargins(4, 4, 4, 4)
        root.setSpacing(4)

        h_split = QSplitter(Qt.Horizontal)
        h_split.setHandleWidth(4)
        root.addWidget(h_split)

        chart_container = QWidget()
        chart_col = QVBoxLayout(chart_container)
        chart_col.setContentsMargins(0, 0, 0, 0)
        chart_col.setSpacing(4)

        chart_panel = PanelFrame("Price Chart - OHLCV")
        self.qt_chart = QtChart(chart_panel.content)
        self.qt_chart.layout(background_color=T["panel"], text_color=T["text_dim"], font_family="Consolas", font_size=11)
        self.qt_chart.grid(vert_enabled=True, horz_enabled=True, color="rgba(42,46,57,0.8)")
        self.qt_chart.crosshair(
            vert_color=T["crosshair"],
            horz_color=T["crosshair"],
            vert_label_background_color=T["surface"],
            horz_label_background_color=T["surface"],
        )
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
        self.qt_chart.time_scale(border_visible=True, border_color=T["border"])
        self.ema_fast_line = self.qt_chart.create_line(name="EMA 9", color=T["ema_fast"], width=1, price_line=False, price_label=False)
        self.ema_slow_line = self.qt_chart.create_line(name="EMA 21", color=T["ema_slow"], width=1, price_line=False, price_label=False)
        chart_panel.content_layout.addWidget(self.qt_chart.get_webview())

        chart_col.addWidget(chart_panel)
        h_split.addWidget(chart_container)

        right_container = QWidget()
        right_col = QVBoxLayout(right_container)
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(4)

        stats_panel = PanelFrame("Market Stats")
        stats_grid = QWidget()
        sg_layout = QHBoxLayout(stats_grid)
        sg_layout.setContentsMargins(10, 8, 10, 8)
        sg_layout.setSpacing(0)

        self._stat_labels = {}
        for key, title in [("high_52w", "52W HIGH"), ("low_52w", "52W LOW"), ("avg_vol", "AVG VOL"), ("candles", "CANDLES")]:
            col = QWidget()
            cl = QVBoxLayout(col)
            cl.setContentsMargins(8, 4, 8, 4)
            cl.setSpacing(2)
            t = QLabel(title)
            t.setStyleSheet(f"color: {T['text_dim']}; font-size: 9px; letter-spacing: 1px;")
            v = QLabel("-")
            v.setStyleSheet(f"color: {T['text_bright']}; font-size: 13px; font-weight: bold;")
            cl.addWidget(t)
            cl.addWidget(v)
            self._stat_labels[key] = v
            sg_layout.addWidget(col)
            if key != "candles":
                sep = QFrame()
                sep.setFrameShape(QFrame.VLine)
                sep.setStyleSheet(f"color: {T['border']};")
                sg_layout.addWidget(sep)

        stats_panel.content_layout.addWidget(stats_grid)
        right_col.addWidget(stats_panel)

        ai_panel = PanelFrame("Multi-Agent Swarm Intelligence")
        self.alert_text = QTextEdit()
        self.alert_text.setReadOnly(True)
        self.alert_text.setFont(QFont("Consolas", 10))
        ai_panel.content_layout.addWidget(self.alert_text)
        right_col.addWidget(ai_panel)

        h_split.addWidget(right_container)
        h_split.setSizes([1100, 380])

    def _blink_live(self):
        self._blink_state = not self._blink_state
        if self._blink_state:
            self.live_badge.setStyleSheet(
                f"background-color: {T['accent']}; border: 1px solid {T['accent']}; "
                "color: white; font-weight: bold; border-radius: 3px; padding: 4px 10px;"
            )
        else:
            self.live_badge.setStyleSheet(
                f"background-color: {T['vol_bear']}; border: 1px solid {T['bear']}; "
                f"color: {T['bear']}; font-weight: bold; border-radius: 3px; padding: 4px 10px;"
            )

    def _extract_time(self, row, fallback_index):
        for key in ("timestamp", "time", "datetime", "date", "ts"):
            if key in row and pd.notna(row[key]):
                ts = pd.to_datetime(row[key], errors="coerce")
                if pd.notna(ts):
                    return ts
        return pd.Timestamp("2000-01-01") + pd.Timedelta(minutes=int(fallback_index))

    def _append_candle(self, o, h, l, c, v, candle_time=None):
        if candle_time is None:
            if self.candle_data:
                candle_time = pd.to_datetime(self.candle_data[-1]["time"]) + pd.Timedelta(minutes=1)
            else:
                candle_time = pd.Timestamp.now()

        candle_time = pd.to_datetime(candle_time, errors="coerce")
        if pd.isna(candle_time):
            candle_time = pd.Timestamp.now()

        self.candle_data.append(
            {
                "time": candle_time,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            }
        )
        self.close_prices.append(float(c))
        self.volume_data.append(float(v))

    def _update_stats(self):
        if not self.close_prices:
            return
        self._stat_labels["high_52w"].setText(f"Rs{max(self.close_prices):.2f}")
        self._stat_labels["low_52w"].setText(f"Rs{min(self.close_prices):.2f}")
        self._stat_labels["avg_vol"].setText(f"{int(np.mean(self.volume_data)):,}")
        self._stat_labels["candles"].setText(str(len(self.candle_data)))

    def redraw_charts(self, fit=False):
        if not self.candle_data:
            return

        df = pd.DataFrame(self.candle_data).sort_values("time").reset_index(drop=True)
        df["ema_fast"] = df["close"].ewm(span=9, adjust=False).mean()
        df["ema_slow"] = df["close"].ewm(span=21, adjust=False).mean()
        self.ema_fast_val = float(df["ema_fast"].iloc[-1])
        self.ema_slow_val = float(df["ema_slow"].iloc[-1])

        self.qt_chart.set(df[["time", "open", "high", "low", "close", "volume"]])
        self.ema_fast_line.set(df[["time", "ema_fast"]].rename(columns={"ema_fast": "EMA 9"}))
        self.ema_slow_line.set(df[["time", "ema_slow"]].rename(columns={"ema_slow": "EMA 21"}))
        if fit:
            self.qt_chart.fit()
        self._update_stats()

    def load_historical_base(self):
        try:
            df = pd.read_parquet("historical_base.parquet")
            df_target = df[df["symbol"] == TARGET_STOCK].copy()

            for idx, row in df_target.iterrows():
                self._append_candle(
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    self._extract_time(row, idx),
                )

            self.redraw_charts(fit=True)
            self.status_left.setText(
                f"  Historical base loaded  ·  {len(self.candle_data)} candles  ·  Kafka {KAFKA_BROKER}"
            )
        except Exception as e:
            self.status_left.setText(f"  Could not load historical_base.parquet: {e}")

    def update_chart(self, raw):
        o = float(raw["open"])
        h = float(raw["high"])
        l = float(raw["low"])
        c = float(raw["close"])
        v = float(raw["volume"])

        candle_time = raw.get("timestamp") or raw.get("time") or raw.get("datetime") or raw.get("date")
        parsed_time = pd.to_datetime(candle_time, errors="coerce")
        if pd.isna(parsed_time):
            parsed_time = pd.Timestamp.now()

        is_same_bar = False
        if self.candle_data:
            last_time = pd.to_datetime(self.candle_data[-1]["time"], errors="coerce")
            is_same_bar = pd.notna(last_time) and (parsed_time == last_time)

        if is_same_bar:
            self.candle_data[-1] = {
                "time": parsed_time,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
            self.close_prices[-1] = c
            self.volume_data[-1] = v
        else:
            self._append_candle(o, h, l, c, v, parsed_time)
        self.ticker_bar.update(o, h, l, c, v)

        # Incremental updates preserve current pan/zoom; avoid full chart reset.
        bar = pd.Series(
            {
                "time": parsed_time,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": v,
            }
        )
        self.qt_chart.update(bar)

        ema_fast = float(pd.Series([item["close"] for item in self.candle_data]).ewm(span=9, adjust=False).mean().iloc[-1])
        ema_slow = float(pd.Series([item["close"] for item in self.candle_data]).ewm(span=21, adjust=False).mean().iloc[-1])
        self.ema_fast_val = ema_fast
        self.ema_slow_val = ema_slow
        self.ema_fast_line.update(pd.Series({"time": parsed_time, "EMA 9": ema_fast}))
        self.ema_slow_line.update(pd.Series({"time": parsed_time, "EMA 21": ema_slow}))

        self._update_stats()
        self.status_right.setText(f"Last update: {len(self.candle_data)} candles  Rs{c:.2f}   ")

    def check_ai_alerts(self):
        if not os.path.exists(ALERTS_FILE):
            return
        try:
            with open(ALERTS_FILE, "r", encoding="utf-8") as f:
                alert = json.load(f)
            if alert.get("timestamp") == self.last_alert_ts:
                return
            self.last_alert_ts = alert["timestamp"]

            html = f"""
<div style="
    margin: 0 0 14px 0;
    padding: 10px 12px;
    background: {T['surface']};
    border-left: 3px solid {T['accent']};
    border-radius: 0 4px 4px 0;
">
  <div style="color:{T['accent']}; font-size:11px; font-weight:bold;
              letter-spacing:1px; margin-bottom:8px;">
      AI ALERT <span style="color:{T['text_dim']}; font-weight:normal;">[{alert['timestamp']}]</span>
  </div>
  <div style="margin-bottom:6px;">
    <span style="color:{T['bull']}; font-weight:bold;">[QUANT]</span>
    <span style="color:{T['text']};">  {alert['quant']}</span>
  </div>
  <div style="margin-bottom:6px;">
    <span style="color:{T['ema_fast']}; font-weight:bold;">[RISK]</span>
    <span style="color:{T['text']};">  {alert['risk']}</span>
  </div>
  <div style="margin-top:8px; padding-top:8px; border-top:1px solid {T['border']}; color:{T['text_bright']}; font-weight:bold;">
    -> {alert['synthesis']}
  </div>
</div>
"""
            self.alert_text.setHtml(html + self.alert_text.toHtml())
        except Exception:
            pass


if __name__ == "__main__":
    app = QApplication(sys.argv)
    app.setStyle("Fusion")
    terminal = TradingTerminal()
    terminal.show()
    sys.exit(app.exec_())
