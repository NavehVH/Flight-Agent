"""
One-off exploration script: connect to Kiwi.com's remote MCP server through
the OpenAI Responses API and print the raw output.

Purpose: Kiwi's MCP tool schema (exact parameter names, and whether a
currency parameter exists) and its response shape (field names for price,
airline, stops, layovers, booking link) aren't documented anywhere public.
The `mcp_list_tools` output item the API returns is the authoritative source
for the tool schema. This script exists to capture that once, so the real
agent code can be written against confirmed field names instead of guesses.

Not part of the agent itself — delete or ignore once the schema is known.
"""

import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / "user-data.txt")

MODEL = "gpt-5.6"  # double-check this is still a valid current model in your account

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

response = client.responses.create(
    model=MODEL,
    tools=[
        {
            "type": "mcp",
            "server_label": "kiwi-flights",
            "server_url": "https://mcp.kiwi.com",
            "require_approval": "never",
        },
    ],
    input=(
        "Call the search-flight tool with flyFrom=TLV, flyTo=Tokyo, "
        "departureDate=05/10/2026, returnDate=20/10/2026, adults=1, "
        "currency=USD. Return the raw tool output, don't summarize."
    ),
)

for item in response.output:
    print("=" * 80)
    print(f"type: {item.type}")
    print(json.dumps(item.model_dump(), indent=2, default=str))

print("=" * 80)
print("FINAL TEXT OUTPUT:")
print(response.output_text)
