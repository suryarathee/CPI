from google.adk.agents import LlmAgent
from google.genai import types


MODEL_NAME = "gemini-2.5-pro"


pattern_analyst_agent = LlmAgent(
    name="pattern_analyst",
    description="Analyzes technical market facts and pattern context for a live trade setup.",
    model=MODEL_NAME,
    instruction=(
        "You are a technical pattern analyst for an algorithmic trading terminal. "
        "Read the provided symbol, price, pattern name, and historical win rate. "
        "Return a concise technical assessment focused on market structure, momentum, "
        "and whether the pattern context looks statistically actionable. "
        "Do not invent indicators that were not provided."
    ),
    generate_content_config=types.GenerateContentConfig(temperature=0.1),
)
