"""End-to-end proof: search Kiwi via OpenAI's MCP tool, map the results,
and store them in SQLite. Run this to sanity-check the whole pipeline
before wiring it into the real agent loop.
"""

import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from db import init_db, insert_offers  # noqa: E402
from kiwi_source import build_tool_input, map_search_result  # noqa: E402

load_dotenv(PROJECT_ROOT / "user-data.txt")

MODEL = "gpt-5.6"  # double-check this is still valid in your account

ORIGIN = "TLV"
DESTINATION = "Tokyo"
DEPARTURE_DATE = "2026-10-05"
RETURN_DATE = "2026-10-20"

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

tool_args = build_tool_input(
    origin=ORIGIN,
    destination=DESTINATION,
    departure_date=DEPARTURE_DATE,
    return_date=RETURN_DATE,
)

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
        f"Call the search-flight tool with exactly these arguments (JSON): "
        f"{tool_args}. Return the raw tool output, don't summarize."
    ),
)

raw_output = None
for item in response.output:
    if item.type == "mcp_call" and item.name == "search-flight":
        raw_output = item.output
        break

if raw_output is None:
    raise RuntimeError("No search-flight tool call found in the response output")

rows = map_search_result(
    raw_output,
    search_outbound_start=DEPARTURE_DATE,
    search_outbound_end=DEPARTURE_DATE,
    search_return_start=RETURN_DATE,
    search_return_end=RETURN_DATE,
    cabin_class=None,
    adults=1,
)

conn = init_db(PROJECT_ROOT / "flights.db")
inserted = insert_offers(conn, rows)
print(f"Inserted {inserted} offers into flights.db")

print("\nCheapest 3 stored offers:")
cur = conn.execute(
    "SELECT price, currency, outbound_airline, outbound_stops, booking_url "
    "FROM flight_offers ORDER BY price ASC LIMIT 3"
)
for row in cur.fetchall():
    print(row)
