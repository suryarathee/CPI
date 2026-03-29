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

from env_loader import load_local_env
from adk_swarm.sub_agents import get_trade_analyst_agent
from adk_swarm.sub_agents.pattern_analyst import get_pattern_analyst_agent
from adk_swarm.sub_agents.risk_analyst import get_risk_analyst_agent
from adk_swarm.sub_agents.algo_architect import get_algo_architect_agent


load_local_env()


APP_NAME = "trading_terminal_swarm"
# Global session service to maintain conversation memory across the lifetime of the application
_global_session_service = InMemorySessionService()


def _get_agent(personality_type: str, model_name: str) -> LlmAgent:
    """
    Returns the requested agent with the specified model.
    """
    if personality_type == "_algo_architect":
        return get_algo_architect_agent(model_name)
    elif personality_type == "Coordinator":
        specialist = get_trade_analyst_agent(model_name)
        return LlmAgent(
            name="trade_alert_coordinator",
            description="Routes technical context to the trade analyst and returns a 3-bullet trade alert.",
            model=model_name,
            instruction=(
                "You coordinate an algorithmic trading terminal. "
                "Use the trade_analyst tool before producing a response. "
                "If this is a new trade alert: Return exactly three plain-English bullet points for a retail trader. "
                "Bullet 1: what triggered. Bullet 2: what the historical edge says. Bullet 3: the practical risk view. "
                "If this is a follow-up question: Answer the user's question concisely based on the technical context provided in the session history. "
                "Stay objective and never promise outcomes."
            ),
            tools=[AgentTool(specialist)],
            generate_content_config=types.GenerateContentConfig(temperature=0.2),
        )
    elif personality_type == "Trade Analyst":
        return get_trade_analyst_agent(model_name)
    elif personality_type == "Pattern Analyst":
        return get_pattern_analyst_agent(model_name)
    elif personality_type == "Risk Analyst":
        return get_risk_analyst_agent(model_name)

    return get_trade_analyst_agent(model_name)


def _has_adk_credentials() -> bool:
    if os.getenv("GOOGLE_API_KEY"):
        return True
    return bool(
        os.getenv("GOOGLE_GENAI_USE_VERTEXAI")
        and os.getenv("GOOGLE_CLOUD_PROJECT")
        and os.getenv("GOOGLE_CLOUD_LOCATION")
    )


async def _run_llm_task(text: str, session_id: str, agent_type: str, model_name: str) -> str:
    agent = _get_agent(agent_type, model_name)
    runner = Runner(app_name=APP_NAME, agent=agent, session_service=_global_session_service)
    user_id = "terminal_user"
    
    # Ensure session exists; ignore if already created
    try:
        await _global_session_service.create_session(app_name=APP_NAME, user_id=user_id, session_id=session_id)
    except Exception as e:
        # If it already exists, that's fine. Otherwise, log it briefly.
        if "already exists" not in str(e).lower():
            print(f"[ADK DEBUG] Session creation note: {e}")

    message = types.Content(
        role="user",
        parts=[types.Part.from_text(text=text)],
    )

    try:
        final_text = ""
        async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=message):
            if not event.content or not event.content.parts:
                continue
            text_parts = [part.text for part in event.content.parts if getattr(part, "text", None)]
            if text_parts:
                final_text += "".join(text_parts)
        return final_text.strip()
    finally:
        await runner.close()


def generate_ui_alert(context_dict: dict[str, Any], agent_type: str = "Coordinator", model_name: str = "gemini-2.0-flash", session_id: str | None = None) -> tuple[str, str]:
    """
    Generates an alert and returns (alert_text, session_id).
    """
    if not _has_adk_credentials():
        # Fallback doesn't use session
        return _fallback_alert(context_dict, RuntimeError("missing Google ADK credentials")), str(uuid.uuid4())

    sid = session_id or str(uuid.uuid4())
    prompt = (
        f"Symbol: {context_dict.get('symbol')}\n"
        f"Price: {context_dict.get('price')}\n"
        f"Screener setup: {context_dict.get('screener_signal')}\n"
        f"Indicator-confirmed pattern: {context_dict.get('pattern_name')}\n"
        f"Indicator context: {context_dict.get('indicator_context', {})}\n"
        f"Historical edge stats: {context_dict.get('edge', {})}\n"
        "Action: Generate a 3-bullet alert."
    )
    
    try:
        response = asyncio.run(_run_llm_task(prompt, sid, agent_type, model_name))
        return response or _fallback_alert(context_dict, RuntimeError("empty ADK response")), sid
    except Exception as exc:
        return _fallback_alert(context_dict, exc), sid


def process_deep_dive_query(query: str, session_id: str, agent_type: str = "Coordinator", model_name: str = "gemini-2.0-flash") -> str:
    """
    Handles follow-up questions for a specific alert session.
    """
    if not _has_adk_credentials():
        return "System Error: Missing Google ADK credentials."
    
    try:
        return asyncio.run(_run_llm_task(query, session_id, agent_type, model_name))
    except Exception as exc:
        return f"Deep-Dive Error: {exc}"


def _fallback_alert(context_dict: dict[str, Any], error: Exception | None = None) -> str:
    symbol = context_dict.get("symbol", "UNKNOWN")
    price = float(context_dict.get("price", 0.0) or 0.0)
    pattern_name = context_dict.get("pattern_name", "Setup")
    edge = context_dict.get("edge", {})
    
    # Normalizing edge stats from different source formats
    win_rate = edge.get("Win Rate [%]", edge.get("win_rate", 0.0))
    if "win_rate" in edge and "Win Rate [%]" not in edge:
        win_rate *= 100.0  # Normalize backtester's 0.0-1.0 to 0-100%
        
    ret_pct = edge.get("Return [%]", edge.get("net_return_pct", 0.0))
    if "net_return_pct" in edge and "Return [%]" not in edge:
        ret_pct *= 100.0
        
    drawdown = edge.get("Max. Drawdown [%]", 0.0)
    trades = edge.get("# Trades", edge.get("total_trades", 0))

    risk_buffer = price * 0.01
    stop_loss = price - risk_buffer
    take_profit = price + (risk_buffer * 2)
    suffix = f" ADK fallback engaged: {error}." if error else ""
    return (
        f"- {symbol} triggered {pattern_name} near {price:.2f} after screener and indicator confirmation.\n"
        f"- Historical edge: Win Rate {win_rate:.2f}%, Return {ret_pct:.2f}%, "
        f"Max Drawdown {drawdown:.2f}%, Trades {trades}.\n"
        f"- Trade only if risk is acceptable; a simple reference plan is stop near {stop_loss:.2f} and target near {take_profit:.2f}.{suffix}"
    )


def generate_algo_code(prompt: str, model_name: str = "gemini-2.0-flash-lite-preview-02-05") -> str:
    """
    Sends a plain-English strategy prompt to the Algo-Architect agent and
    returns the raw Python class string.
    """
    if not _has_adk_credentials():
        return _fallback_algo_code(prompt)

    sid = str(uuid.uuid4())
    instruction = (
        f"Translate this trading strategy into a GeneratedStrategy class:\n\n{prompt}"
    )
    try:
        code = asyncio.run(_run_llm_task(instruction, sid, "_algo_architect", model_name))
        return code.strip() if code else _fallback_algo_code(prompt)
    except Exception as exc:
        return _fallback_algo_code(prompt, error=exc)


def _fallback_algo_code(prompt: str, error: Exception | None = None) -> str:
    """Returns a minimal EMA crossover strategy when the ADK call fails."""
    suffix = f"\n# ADK Fallback — error: {error}" if error else ""
    return (
        "class GeneratedStrategy(Strategy):\n"
        "    def init(self):\n"
        "        close = self.data.Close\n"
        "        self.ema_fast = self.I(lambda x, n: pd.Series(x).ewm(span=n, adjust=False).mean().values, close, 9)\n"
        "        self.ema_slow = self.I(lambda x, n: pd.Series(x).ewm(span=n, adjust=False).mean().values, close, 21)\n"
        "\n"
        "    def next(self):\n"
        "        if crossover(self.ema_fast, self.ema_slow):\n"
        "            price = self.data.Close[-1]\n"
        "            self.buy(sl=price * 0.98, tp=price * 1.04)\n"
        "        elif crossover(self.ema_slow, self.ema_fast):\n"
        "            self.position.close()\n"
        + suffix
    )


def to_alert_html(alert_text: str) -> str:
    safe = escape(alert_text).replace("\n", "<br>")
    return (
        "<div style='margin:0 0 12px 0;padding:10px 12px;background:#161B24;"
        "border-left:3px solid #FF5733;border-radius:0 3px 3px 0;'>"
        f"{safe}</div>"
    )
