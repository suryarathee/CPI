from google.adk.agents import LlmAgent
from google.genai import types

def get_risk_analyst_agent(model_name: str) -> LlmAgent:
    return LlmAgent(
        name="risk_analyst",
        description="Defines strict 1:2 risk/reward stop-loss and target guidance for live trades.",
        model=model_name,
        instruction=(
            "You are a strict risk analyst. For the provided symbol, current price, pattern, and win rate, "
            "define a hard stop-loss and a target using an exact 1:2 risk/reward framework. "
            "Use concrete price levels derived from the current price. "
            "Keep the response concise, disciplined, and execution-oriented."
        ),
        generate_content_config=types.GenerateContentConfig(temperature=0.1),
    )
