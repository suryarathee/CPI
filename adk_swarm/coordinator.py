from __future__ import annotations

import asyncio
import os
import uuid
from html import escape
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from adk_swarm.sub_agents.trade_analyst import trade_analyst_agent
from env_loader import load_local_env


load_local_env()


APP_NAME = "trading_terminal_swarm"
MODEL_NAME = "gemini-2.5-pro"


root_agent = LlmAgent(
    name="trade_alert_coordinator",
    description="Routes technical context to the trade analyst and returns a 3-bullet trade alert for the UI.",
    model=MODEL_NAME,
    instruction=(
        "You coordinate an algorithmic trading terminal. "
        "Use the trade_analyst tool before producing a response. "
        "Return exactly three plain-English bullet points for a retail trader. "
        "Bullet 1: what triggered. Bullet 2: what the historical edge says. Bullet 3: the practical risk view. "
        "Stay objective and never promise outcomes."
    ),
    tools=[AgentTool(trade_analyst_agent)],
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)


def _has_adk_credentials() -> bool:
    if os.getenv("GOOGLE_API_KEY"):
        return True
    return bool(
        os.getenv("GOOGLE_GENAI_USE_VERTEXAI")
        and os.getenv("GOOGLE_CLOUD_PROJECT")
        and os.getenv("GOOGLE_CLOUD_LOCATION")
    )


def _format_request(context_dict: dict[str, Any]) -> str:
    indicator_context = context_dict.get("indicator_context", {})
    edge = context_dict.get("edge", {})
    return (
        f"Symbol: {context_dict.get('symbol')}\n"
        f"Price: {context_dict.get('price')}\n"
        f"Screener setup: {context_dict.get('screener_signal')}\n"
        f"Indicator-confirmed pattern: {context_dict.get('pattern_name')}\n"
        f"Indicator context: {indicator_context}\n"
        f"Historical edge stats: {edge}\n"
        "Use the trade_analyst tool first, then produce exactly three bullet points."
    )


def _fallback_alert(context_dict: dict[str, Any], error: Exception | None = None) -> str:
    symbol = context_dict.get("symbol", "UNKNOWN")
    price = float(context_dict.get("price", 0.0) or 0.0)
    pattern_name = context_dict.get("pattern_name", "Setup")
    edge = context_dict.get("edge", {})
    risk_buffer = price * 0.01
    stop_loss = price - risk_buffer
    take_profit = price + (risk_buffer * 2)
    suffix = f" ADK fallback engaged: {error}." if error else ""
    return (
        f"- {symbol} triggered {pattern_name} near {price:.2f} after screener and indicator confirmation.\n"
        f"- Historical edge: Win Rate {edge.get('Win Rate [%]', 0.0):.2f}%, Return {edge.get('Return [%]', 0.0):.2f}%, "
        f"Max Drawdown {edge.get('Max. Drawdown [%]', 0.0):.2f}%, Trades {edge.get('# Trades', 0)}.\n"
        f"- Trade only if risk is acceptable; a simple reference plan is stop near {stop_loss:.2f} and target near {take_profit:.2f}.{suffix}"
    )


async def _run_async(context_dict: dict[str, Any]) -> str:
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)
    user_id = "dashboard"
    session_id = str(uuid.uuid4())
    await session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)

    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=_format_request(context_dict))],
    )

    try:
        final_text = ""
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
            if not event.content or not event.content.parts:
                continue
            text_parts = [part.text for part in event.content.parts if getattr(part, "text", None)]
            if text_parts:
                final_text = "\n".join(text_parts).strip()
        return final_text
    finally:
        await runner.close()


def generate_ui_alert(context_dict: dict[str, Any]) -> str:
    if not _has_adk_credentials():
        return _fallback_alert(context_dict, RuntimeError("missing Google ADK credentials"))

    try:
        response = asyncio.run(_run_async(context_dict))
        return response or _fallback_alert(context_dict, RuntimeError("empty ADK response"))
    except Exception as exc:
        return _fallback_alert(context_dict, exc)


def to_alert_html(alert_text: str) -> str:
    safe = escape(alert_text).replace("\n", "<br>")
    return (
        "<div style='margin:0 0 12px 0;padding:10px 12px;background:#161B24;"
        "border-left:3px solid #FF5733;border-radius:0 3px 3px 0;'>"
        f"{safe}</div>"
    )
