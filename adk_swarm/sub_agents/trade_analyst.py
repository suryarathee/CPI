from __future__ import annotations

from google.adk.agents import LlmAgent
from google.genai import types


MODEL_NAME = "gemini-2.5-pro"


trade_analyst_agent = LlmAgent(
    name="trade_analyst",
    description="Evaluates technical confirmation and historical edge stats to assess trade viability and risk.",
    model=MODEL_NAME,
    instruction=(
        "You are a professional trade analyst inside an algorithmic trading terminal. "
        "Read the screener setup, PyIndicators confirmation details, price context, and historical backtest metrics. "
        "Judge whether the setup is tradeable, what the risk quality looks like, and what a retail trader should watch. "
        "Be concise, concrete, and avoid hype."
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.15),
)
