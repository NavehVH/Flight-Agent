# Flight Agent

An agent that searches for cheap TLV → Tokyo round-trip flights matching a
specific set of real requirements, stores everything it finds, and emails
a plain-language report of every matching option — no LLM guesswork on the
final numbers, no manual searching.

Built for one concrete use case: finding my dad a flight. Current search
targets his actual ask —

> "we need 2 tickets to japan in november for minimum 3 weeks, boarding and
> landing in tokyo, doesnt have to be a direct flight but not more than one
> switch"

## How it works

```
run_agent.py
     │
     ▼
1. Compute a search plan in code — deterministically tile the outbound
   date range into non-overlapping search windows (no gaps, no reliance
   on the model deciding "that's enough coverage")
     │
     ▼
2. Loop: ask the model (gpt-4.1-mini) to run one search per turn against
   Kiwi.com's flight-search MCP server, until every planned search is done
     │         │
     │         ▼
     │    Every result is auto-saved to SQLite — the model is never asked
     │    to do this, it just happens as a side effect of searching
     │
     ▼
3. Once searching is done, plain Python (no LLM) queries SQLite for every
   offer that actually satisfies the hard constraints (stops, trip length,
   date range) and formats a report
     │
     ▼
4. Real email sent, plus a local output.txt copy
```

The model's only job is running searches — picking the cheapest offer,
filtering by constraints, and formatting the report are all deterministic
code, not something an LLM is asked to decide. Full design rationale and
the mistakes that led here are in `CLAUDE.md`.

### Data flow at a glance

| File | Role |
|---|---|
| `run_agent.py` | Orchestrates everything: builds the search plan, runs the turn-by-turn agent loop, tracks cost |
| `kiwi_source.py` | The only file that knows Kiwi's specific request/response shapes |
| `db.py` | Generic SQLite persistence (`flight_offers`, `agent_runs` tables) |
| `report.py` | Filters stored offers against hard constraints and formats the report — no model involved |
| `email_sender.py` | Sends the report via Gmail SMTP |
| `schema.sql` | Table definitions |
| `scripts/` | One-off exploration scripts used while building this (not part of the running agent) |

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Create `user-data.txt` in the project root (already gitignored) with:

```
EMAIL_USER=you@gmail.com
EMAIL_PASS=your-gmail-app-password
RECIPIENT_EMAIL=where-to-send-the-report@example.com
OPENAI_API_KEY=sk-...
```

`EMAIL_PASS` must be a [Gmail App Password](https://myaccount.google.com/apppasswords),
not your regular account password.

No API key is needed for flight search — Kiwi.com's MCP server
(`https://mcp.kiwi.com`) is free and open, no account required.

## Running it

```bash
.venv/bin/python run_agent.py
```

This searches, stores every result in `flights.db`, and emails
`RECIPIENT_EMAIL` a report of every matching flight found. Console output
shows progress turn-by-turn, including running token/cost totals.

## Configuration

There's no CLI yet — trip parameters are constants at the top of
`run_agent.py`:

| Constant | Meaning |
|---|---|
| `ORIGIN`, `DESTINATION` | Airport/city names or IATA codes |
| `ADULTS` | Passenger count |
| `OUTBOUND_RANGE_START` / `_END` | Departure date window to search across |
| `MIN_TRIP_NIGHTS` | Minimum days between outbound and return |
| `MAX_STOPS` | Maximum stops allowed per leg |

Edit these directly and re-run for a different search.

## Data

- **`flights.db`** — SQLite database (gitignored). `flight_offers` accumulates
  every offer ever found, across all runs, so price history builds up over
  time. `agent_runs` logs cost/behavior telemetry per run.
- **`output.txt`** — a local copy of the most recent report (gitignored).

## Cost

A full run (5 searches covering all of November) costs roughly **$0.30–0.40**
in OpenAI API usage, hard-capped at `MAX_ESTIMATED_COST_USD` in
`run_agent.py` regardless. `gpt-4.1-mini` is used deliberately — this task
is tool orchestration, not reasoning, so a reasoning-tier model would only
add cost with no benefit.

## Known limitations

- Kiwi's MCP server is officially "prototype" status — it can be flaky
  (occasional `424` errors) or change without notice. The pipeline is
  built to fail gracefully if it does (a report still gets sent, just
  noting nothing was found).
- One-shot only — no scheduled/recurring mode yet.
- Single data source. A second source (e.g. SerpAPI's Google Flights data)
  would help cross-check results but isn't built yet.
