"""
sandbox_runner.py
-----------------
A secure, sandboxed execution engine that:
  1. Accepts AI-generated Python code (a backtesting.Strategy subclass string).
  2. Compiles and runs it safely inside a restricted globals dict.
  3. Returns structured backtest statistics or a graceful error dict.

Security model:
  - exec() is called with a tightly scoped globals_dict — no access to
    os, sys, subprocess, open, __builtins__ write paths, etc.
  - A hard wall-clock timeout is enforced via threading to prevent the AI
    from producing an infinite loop that hangs the worker thread forever.
  - All exceptions (SyntaxError, NameError, RuntimeError, …) are caught and
    returned as a structured error dict so the UI always gets a response.
"""
from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from backtesting import Backtest, Strategy
from backtesting.lib import crossover, cross

PARQUET_DIR = Path(__file__).resolve().parent / "parquet_data"

# ── Timeout sentinel ──────────────────────────────────────────────────────────
SANDBOX_TIMEOUT_SECONDS = 60  # Wall-clock limit for the entire run() call

# ── Pre-built safe globals injected into every exec() call ────────────────────
def _build_safe_globals() -> dict[str, Any]:
    """
    Returns a restricted namespace with only the tools the generated strategy
    needs. Crucially, __builtins__ is stripped to a minimal read-only set so
    the code cannot call open(), exec(), import, etc.
    """
    safe_builtins = {
        # ── Class-definition machinery (REQUIRED for `class` keyword) ─────
        "__build_class__": __build_class__,  # powers `class Foo:` syntax
        "__name__": __name__,                # needed for class __module__
        # ── Basic types / introspection ───────────────────────────────────
        "True": True,
        "False": False,
        "None": None,
        "abs": abs,
        "bool": bool,
        "dict": dict,
        "enumerate": enumerate,
        "float": float,
        "int": int,
        "isinstance": isinstance,
        "issubclass": issubclass,
        "len": len,
        "list": list,
        "max": max,
        "min": min,
        "print": print,     # safe read-only logging
        "property": property,
        "range": range,
        "round": round,
        "sorted": sorted,
        "staticmethod": staticmethod,
        "str": str,
        "sum": sum,
        "super": super,     # needed for Strategy subclass __init__ chains
        "tuple": tuple,
        "type": type,
        "zip": zip,
        # ── Math ──────────────────────────────────────────────────────────
        "pow": pow,
        "NotImplementedError": NotImplementedError,
        "ValueError": ValueError,
        "TypeError": TypeError,
        "ZeroDivisionError": ZeroDivisionError,
    }
    return {
        "__builtins__": safe_builtins,
        # Third-party / stdlib allowed
        "np": np,
        "pd": pd,
        "Strategy": Strategy,
        "crossover": crossover,
        "cross": cross,
    }


def _load_symbol_df(symbol: str) -> pd.DataFrame:
    """Loads and standardises a symbol's parquet file for backtesting.py."""
    parquet_path = PARQUET_DIR / f"{symbol}.parquet"
    if not parquet_path.exists():
        # Try a fallback to any available parquet
        available = sorted(PARQUET_DIR.glob("*.parquet"))
        if not available:
            raise FileNotFoundError("No parquet files found in parquet_data/")
        parquet_path = available[0]
        symbol = parquet_path.stem

    df = pd.read_parquet(parquet_path)

    # Standardise column names
    rename_map = {
        "timestamp": "Date",
        "date": "Date",
        "datetime": "Date",
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})

    if "Date" in df.columns:
        df["Date"] = pd.to_datetime(df["Date"], utc=True, errors="coerce")
        df = df.set_index("Date").sort_index()

    required = ["Open", "High", "Low", "Close", "Volume"]
    for col in required:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.dropna(subset=required)[required].copy()


def _sanitize_generated_code(code: str) -> str:
    """
    Strips markdown fences that the LLM sometimes emits despite being told not to.
    Also strips leading/trailing whitespace.
    """
    code = code.strip()
    # Remove ```python ... ``` or ``` ... ```
    if code.startswith("```"):
        lines = code.splitlines()
        # Drop first line (```python or ```)
        lines = lines[1:]
        # Drop last closing fence
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        code = "\n".join(lines).strip()
    return code


def _extract_stats(stats: Any, symbol: str, code_snippet: str) -> dict:
    """Converts backtesting.py stats _Stats object to a plain dict."""
    trades_df = stats.get("_trades", pd.DataFrame())
    total = int(stats.get("# Trades", 0))
    win_rate = float(stats.get("Win Rate [%]", 0.0) or 0.0)
    ret_pct = float(stats.get("Return [%]", 0.0) or 0.0)
    sharpe = float(stats.get("Sharpe Ratio", 0.0) or 0.0)
    max_dd = float(stats.get("Max. Drawdown [%]", 0.0) or 0.0)
    expectancy = float(stats.get("Expectancy [%]", 0.0) or 0.0)
    final_equity = float(stats.get("Equity Final [$]", 10_000.0) or 10_000.0)

    # Win/Loss streaks
    pnl_arr = trades_df["PnL"].values if not trades_df.empty else []
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for pnl in pnl_arr:
        if pnl > 0:
            cur_win += 1
            cur_loss = 0
            max_win_streak = max(max_win_streak, cur_win)
        else:
            cur_loss += 1
            cur_win = 0
            max_loss_streak = max(max_loss_streak, cur_loss)

    return {
        "ok": True,
        "symbol": symbol,
        "total_trades": total,
        "win_rate": win_rate / 100.0,
        "win_rate_pct": win_rate,
        "return_pct": ret_pct,
        "expectancy_pct": expectancy,
        "sharpe_ratio": sharpe,
        "max_drawdown_pct": max_dd,
        "final_equity": final_equity,
        "max_win_streak": max_win_streak,
        "max_loss_streak": max_loss_streak,
        "code_preview": code_snippet[:300],
    }


def run_dynamic_strategy(generated_code: str, symbol: str = "RELIANCE") -> dict:
    """
    Safely compiles and runs the AI-generated strategy string.

    Parameters
    ----------
    generated_code : str
        Raw Python source for a class named ``GeneratedStrategy`` that
        inherits from ``Strategy``.
    symbol : str
        NSE symbol whose parquet file will be used for the backtest.

    Returns
    -------
    dict
        On success: ``{"ok": True, "total_trades": ..., "win_rate": ..., ...}``
        On failure: ``{"ok": False, "error": "<message>", "traceback": "..."}``
    """
    code = _sanitize_generated_code(generated_code)
    code_preview = code[:200]

    # ── 1. Syntax check ───────────────────────────────────────────────────────
    try:
        compiled = compile(code, "<algo_architect>", "exec")
    except SyntaxError as exc:
        return {
            "ok": False,
            "error": f"SyntaxError: {exc}",
            "traceback": traceback.format_exc(),
            "code_preview": code_preview,
        }

    # ── 2. Execute in sandbox ─────────────────────────────────────────────────
    globals_dict = _build_safe_globals()
    try:
        exec(compiled, globals_dict)  # noqa: S102
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Execution error: {type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "code_preview": code_preview,
        }

    # ── 3. Extract class ──────────────────────────────────────────────────────
    GeneratedStrategy = globals_dict.get("GeneratedStrategy")
    if GeneratedStrategy is None:
        return {
            "ok": False,
            "error": "Agent did not produce a class named 'GeneratedStrategy'.",
            "traceback": "",
            "code_preview": code_preview,
        }

    # Validate it's a proper Strategy subclass
    try:
        if not issubclass(GeneratedStrategy, Strategy):
            return {
                "ok": False,
                "error": "'GeneratedStrategy' does not inherit from backtesting.Strategy.",
                "traceback": "",
                "code_preview": code_preview,
            }
    except TypeError as exc:
        return {
            "ok": False,
            "error": f"Class validation failed: {exc}",
            "traceback": traceback.format_exc(),
            "code_preview": code_preview,
        }

    # ── 4. Load data ──────────────────────────────────────────────────────────
    try:
        df = _load_symbol_df(symbol)
    except Exception as exc:
        return {
            "ok": False,
            "error": f"Data load error for '{symbol}': {exc}",
            "traceback": traceback.format_exc(),
            "code_preview": code_preview,
        }

    if len(df) < 50:
        return {
            "ok": False,
            "error": f"Insufficient data for '{symbol}': only {len(df)} rows.",
            "traceback": "",
            "code_preview": code_preview,
        }

    # ── 5. Run backtest with timeout ──────────────────────────────────────────
    result_holder: list[dict] = []
    exception_holder: list[Exception] = []

    def _run():
        try:
            bt = Backtest(
                df,
                GeneratedStrategy,
                cash=100_000,
                commission=0.0005,
                trade_on_close=True,
                exclusive_orders=True,
                finalize_trades=True,
            )
            stats = bt.run()
            result_holder.append(_extract_stats(stats, symbol, code_preview))
        except Exception as exc:  # noqa: BLE001
            exception_holder.append(exc)

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout=SANDBOX_TIMEOUT_SECONDS)

    if thread.is_alive():
        # Thread is still running past deadline — we can't kill it cleanly in
        # CPython, but we mark the result as a timeout and move on.
        return {
            "ok": False,
            "error": (
                f"Backtest timed out after {SANDBOX_TIMEOUT_SECONDS}s. "
                "The generated strategy may contain an infinite loop or very heavy computation."
            ),
            "traceback": "",
            "code_preview": code_preview,
        }

    if exception_holder:
        exc = exception_holder[0]
        return {
            "ok": False,
            "error": f"Backtest runtime error: {type(exc).__name__}: {exc}",
            "traceback": traceback.format_exc(),
            "code_preview": code_preview,
        }

    return result_holder[0] if result_holder else {
        "ok": False,
        "error": "Unknown error: backtest produced no result.",
        "traceback": "",
        "code_preview": code_preview,
    }
