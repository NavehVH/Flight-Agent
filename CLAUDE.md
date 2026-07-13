# Flight Agent

## Purpose

An autonomous agent that finds cheap flights from Israel to Japan and reports
the best deal it finds. Built to be handed to a non-technical end user (my
dad) — it should be simple to run and produce a clear, readable answer, not
just raw data.

## Core objective

Given a date range (e.g. "outbound between Oct 1–15, return between Oct 20–31"),
the agent:

1. Searches for flights from Israel (TLV) to Japan (NRT/HND, or nearby
   airports) within that range.
2. Saves every flight option it finds to a local SQLite database, so results
   accumulate over time and history is queryable later.
3. Determines the cheapest available option(s) across the range and surfaces
   them clearly.

## Architecture (decided)

- **LLM / agent loop**: OpenAI Responses API. The agent loop is a hand-written
  manual loop (call model → inspect tool calls → execute → feed results back
  → repeat) rather than a framework-managed loop — this is deliberate, to
  keep the internals visible and explainable rather than hidden inside a
  higher-level abstraction.
- **Flight data source**: [Kiwi.com's official MCP server](https://www.pulsemcp.com/servers/kiwi-flights)
  (remote, Streamable HTTP, free, no API key or account required — announced
  Aug 2025, currently "prototype" status per Kiwi). Exposes flight search
  (one-way/round-trip, origin/destination, dates, passengers, cabin class)
  with results in local currency and booking links. Connected via the
  Responses API's remote MCP tool support. Note: prototype status means it
  could change or go away — if it becomes unreliable, fall back to a direct
  Amadeus or Kiwi Tequila API integration (same custom-tool pattern, just
  with an API key to manage).
- **Search strategy**: the agent plans and issues multiple flight searches
  across the given date range (not a single fixed date pair) — e.g. trying
  different outbound/return combinations — and tracks all results.
- **Persistence**: every flight offer found is saved to SQLite (custom
  client-side tool, since the MCP server only searches — it doesn't persist
  anything). One row per (flight offer, date searched), including at least
  price, airline, outbound/return dates, layovers, and the timestamp the
  price was observed. This is what lets "cheapest found" be computed across
  the whole date range and lets price history accumulate over time.
- **Operating mode (current)**: one-shot — run a command with a date range,
  the agent searches, stores results, and reports the cheapest option found
  in that run. A scheduled/autonomous monitoring mode (e.g. daily cron,
  alert only on a new low) is a possible later extension, not being built
  yet.
- **Delivery**: results are emailed (not just printed to a terminal) so my
  dad doesn't need to touch a command line — he receives a plain-language
  report: cheapest flight, price, airline, dates, layover info. For now a
  human (me) runs the command; only the output delivery is dad-facing.

## Target user

Not a developer. Setup and running the tool is my job; my dad's only
interaction is receiving the email report. Error messages and setup steps
should be forgiving and clear regardless.

## Status

Greenfield — no code yet. Next steps: confirm the Kiwi MCP server's exact
tool schema, define the SQLite schema, set up email sending (SMTP), then
build the manual agent loop (search → store → pick cheapest → email report).
