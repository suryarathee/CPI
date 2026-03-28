from __future__ import annotations

import asyncio
import os
import uuid

from google.adk.agents import LlmAgent
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.agent_tool import AgentTool
from google.genai import types

from adk_swarm.sub_agents.pattern_analyst.agent import pattern_analyst_agent
from adk_swarm.sub_agents.risk_analyst.agent import risk_analyst_agent


APP_NAME = "trading_terminal_swarm"
MODEL_NAME = "gemini-2.0-flash"


root_agent = LlmAgent(
    name="trade_event_coordinator",
    description="Coordinates technical and risk specialists, then synthesizes one plain-English trade alert.",
    model=MODEL_NAME,
    instruction=(
        "You are the lead coordinator for an algorithmic trading terminal. "
        "Always consult the pattern_analyst and risk_analyst tools before responding. "
        "Synthesize their outputs with the supplied historical win rate into a plain-English trade alert. "
        "Your response must be crisp, objective, and directly actionable for a trader. "
        "Mention the pattern, current price, historical win rate, and the risk plan."
    ),
    tools=[
        AgentTool(pattern_analyst_agent),
        AgentTool(risk_analyst_agent),
    ],
    generate_content_config=types.GenerateContentConfig(temperature=0.2),
)


def _build_request(symbol: str, current_price: float, pattern_name: str, win_rate: float) -> str:
    return (
        f"Symbol: {symbol}\n"
        f"Current price: {current_price:.2f}\n"
        f"Pattern: {pattern_name}\n"
        f"Historical win rate from backtester: {win_rate:.2%}\n"
        "Consult the specialist tools and produce a single plain-English trade alert."
    )


def _fallback_alert(symbol: str, current_price: float, pattern_name: str, win_rate: float, error: Exception) -> str:
    risk_per_share = current_price * 0.01
    stop_loss = current_price - risk_per_share
    target = current_price + (2 * risk_per_share)
    return (
        f"{pattern_name} detected on {symbol} near {current_price:.2f}. "
        f"Historical win rate is {win_rate:.2%}. "
        f"Use a strict 1:2 plan with stop-loss near {stop_loss:.2f} and target near {target:.2f}. "
        f"ADK synthesis fallback engaged because the coordinator call failed: {error}."
    )


def _has_adk_credentials() -> bool:
    if os.getenv("GOOGLE_API_KEY"):
        return True
    return bool(
        os.getenv("GOOGLE_GENAI_USE_VERTEXAI")
        and os.getenv("GOOGLE_CLOUD_PROJECT")
        and os.getenv("GOOGLE_CLOUD_LOCATION")
    )


async def _run_coordinator_async(symbol: str, current_price: float, pattern_name: str, win_rate: float) -> str:
    session_service = InMemorySessionService()
    runner = Runner(app_name=APP_NAME, agent=root_agent, session_service=session_service)

    user_id = "terminal"
    session_id = str(uuid.uuid4())
    await session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)

    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=_build_request(symbol, current_price, pattern_name, win_rate))],
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


def analyze_trade_event(symbol, current_price, pattern_name, win_rate):
    if not _has_adk_credentials():
        return _fallback_alert(
            symbol,
            current_price,
            pattern_name,
            win_rate,
            RuntimeError("missing Google ADK credentials"),
        )

    try:
        final_text = asyncio.run(_run_coordinator_async(symbol, current_price, pattern_name, win_rate))
        return final_text or _fallback_alert(symbol, current_price, pattern_name, win_rate, RuntimeError("empty ADK response"))
    except Exception as exc:
        return _fallback_alert(symbol, current_price, pattern_name, win_rate, exc)
