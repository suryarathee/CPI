from __future__ import annotations

import json
from pathlib import Path


DATA_DIR = Path(__file__).resolve().parent / "parquet_data"
CONFIG_PATH = Path(__file__).resolve().parent / "runtime_selection.json"
DEFAULT_SYMBOL = "RELIANCE"


def available_symbols() -> list[str]:
    if not DATA_DIR.exists():
        return [DEFAULT_SYMBOL]

    symbols = sorted(path.stem for path in DATA_DIR.glob("*.parquet") if path.is_file())
    return symbols or [DEFAULT_SYMBOL]


def load_selection() -> dict:
    symbols = available_symbols()
    default_payload = {"selected_symbols": [DEFAULT_SYMBOL], "focus_symbol": DEFAULT_SYMBOL}

    if not CONFIG_PATH.exists():
        save_selection(default_payload["selected_symbols"], default_payload["focus_symbol"])
        return default_payload

    try:
        payload = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        save_selection(default_payload["selected_symbols"], default_payload["focus_symbol"])
        return default_payload

    selected = [symbol for symbol in payload.get("selected_symbols", []) if symbol in symbols]
    if not selected:
        selected = [DEFAULT_SYMBOL] if DEFAULT_SYMBOL in symbols else [symbols[0]]

    focus_symbol = payload.get("focus_symbol")
    if focus_symbol not in selected:
        focus_symbol = selected[0]

    normalized = {"selected_symbols": selected, "focus_symbol": focus_symbol}
    save_selection(normalized["selected_symbols"], normalized["focus_symbol"])
    return normalized


def save_selection(selected_symbols: list[str], focus_symbol: str | None = None) -> dict:
    symbols = available_symbols()
    normalized = [symbol for symbol in selected_symbols if symbol in symbols]
    if not normalized:
        normalized = [DEFAULT_SYMBOL] if DEFAULT_SYMBOL in symbols else [symbols[0]]

    focus = focus_symbol if focus_symbol in normalized else normalized[0]
    payload = {"selected_symbols": normalized, "focus_symbol": focus}
    CONFIG_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
