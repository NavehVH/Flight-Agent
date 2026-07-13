# Flight Agent

## Purpose

An autonomous agent that finds cheap flights from Israel to Japan and reports
the best deal it finds. Built to be handed to a non-technical end user (my
dad) — it should be simple to run and produce a clear, readable answer, not
just raw data.

## Core objective

Given a date range, the agent:

1. Searches for flights from Israel (TLV) to Japan (NRT/HND, or nearby
   airports) within that range.
2. Saves every flight option it finds to a local SQLite database, so results
   accumulate over time and history is queryable later.
3. Filters to whatever satisfies the trip's actual hard constraints and
   reports every matching option — not just "the cheapest," since dad wants
   to see and compare the full matching set himself, not just be told one
   number.

### Real requirements (from dad, verbatim: "we need 2 tickets to japan in
november for minimum 3 weeks, boarding and landing in tokyo, doesnt have to
be a direct flight but not more than one switch")

- 2 passengers (`adults=2` on every search)
- Outbound departure anywhere in November; return at least 21 days
  (3 weeks) after *that specific* departure date — not two independent
  fixed ranges. Enforced twice: loosely in the search instructions (so the
  model searches sensible pairings), and strictly against the real
  returned dates in `report.py` (since a search's flex window can return
  itineraries that don't actually satisfy the 21-day minimum).
- Both ends at Tokyo (already the default — `DESTINATION = "Tokyo"` used
  for both the outbound arrival and return departure).
- At most 1 stop per leg ("not more than one switch"). Kiwi's
  `search-flight` tool has **no stops filter** — this is enforced entirely
  in `report.py`, not something the model can search for.

## Architecture (decided)

- **Model**: `gpt-4.1-mini` — deliberately not a reasoning-tier model (the
  gpt-5.6 family). This task is tool orchestration (run a fixed list of
  searches, nothing more) — not multi-step logical reasoning, so a
  reasoning model buys nothing here except hidden reasoning tokens billed
  at a premium output rate. A first real run on `gpt-5.6-sol` (reasoning-
  tier, most expensive) cost roughly $1-1.50 for one 8-turn search.
  Plain `gpt-4.1` was tried next but turned out to have an outlier-low 30k
  tokens/minute rate limit on this account — confirmed by checking
  `x-ratelimit-limit-tokens` headers across models: every other model
  (`gpt-4.1-mini`, all three `gpt-5.6` tiers) sits at 200k-500k on the same
  account. Switched to `gpt-4.1-mini`: same non-reasoning family, cheaper
  per-token than plain `gpt-4.1` ($0.40/$1.60 vs $2/$8 per 1M), and no rate
  limit problem at all — a full 5-search run completes in one go with no
  429s. If tool-use reliability ever turns out worse than the reasoning
  tier delivered, that's the tradeoff to revisit.
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
  could change or go away — if it becomes unreliable, fall back to SerpAPI's
  Google Flights data, called as a plain REST custom tool (not via MCP, since
  the MCP wrappers for it are local/stdio-only). Amadeus is not viable as a
  fallback — its free self-service API shut down July 17, 2026.
- **Search strategy**: the agent plans and issues multiple flight searches
  across the given date range (not a single fixed date pair) — e.g. trying
  different outbound/return combinations — and tracks all results.
- **Persistence**: every flight offer found is saved to SQLite (custom
  client-side tool, since the MCP server only searches — it doesn't persist
  anything). Schema in `schema.sql` — one flat, append-only `flight_offers`
  table (no upserts, every search run just inserts new rows), covering
  route, outbound/return legs (each with its own airline, stops, layover
  airports), trip type, price + currency, cabin class, booking URL, and
  `observed_at`/`source` so price history accumulates over time and multiple
  data sources can coexist.
- **Currency**: all stored/compared prices are standardized to **USD**.
  Confirmed Kiwi's `search-flight` tool takes a `currency` param (default
  `"EUR"`) — every search explicitly passes `currency=USD`, verified
  against real responses. Without this, "cheapest" comparisons across rows
  aren't trustworthy.
- **Operating mode (current)**: one-shot — run a command, the agent
  executes a fixed search plan, stores results, and reports every matching
  option (see Core objective — not just "the cheapest"). A scheduled/
  autonomous monitoring mode (e.g. daily cron, alert only on a new low) is
  a possible later extension, not being built yet.
- **Delivery**: live — `deliver_report()` actually emails `RECIPIENT_EMAIL`
  (not just a terminal print) so my dad doesn't need to touch a command
  line; he gets a plain-language list of every matching flight. A local
  `output.txt` copy is also kept as a debug/audit trail. For now a human
  (me) runs the command; only the output delivery is dad-facing.

## Agent loop design

**Revised after real cost/rate-limit testing.** Originally had three roles
(MCP search tool, two custom function tools the model called, and
deterministic auto-persistence). Cut down to two after two findings:

1. Combining the MCP tool with custom function tools in one request used
   roughly 10x the tokens of the MCP tool alone (2,497 vs. ~25,000 tokens,
   confirmed by isolated testing) — enough to blow through this account's
   30k-tokens/minute rate limit for `gpt-4.1` on turn one.
2. More importantly: neither custom tool needed to be a model-callable tool
   at all. `query_cheapest_offers` was just `SELECT ... ORDER BY price`;
   `send_email_report`'s content was known structured data (price, dates,
   airline, link) being formatted into text. Neither is a judgment call —
   they're exactly the kind of thing that should be plain code, not an LLM
   decision paid for per-token.

Current design:

1. **MCP tool (`search-flight` on Kiwi's remote server)** — the model's
   *only* tool. **Revised again**: which date pairs to search is no longer
   left to the model's judgment either. First attempt let it "choose a
   handful... so the full month is adequately covered" — it searched 3
   dates clustered in the middle of November and decided that was enough,
   leaving both ends of the month unsearched. Search coverage is a
   correctness requirement, not a judgment call, so `_generate_search_plan()`
   now deterministically tiles the outbound range into non-overlapping
   anchors (each anchor's `+/-FLEX_DAYS` window covers `2*FLEX_DAYS+1` days,
   spaced to exactly tile with no gaps) and the model is given the literal
   list of date pairs to search, in order. `MAX_SEARCH_CALLS` is `len(SEARCH_PLAN)`
   — the plan itself is the stop condition. If the model pauses before
   finishing the list, the loop nudges it to continue rather than accepting
   an early stop; it only actually stops once every planned search has run.
2. **Deterministic auto-persistence** (during the loop) — every turn, the
   loop scans `response.output` for `mcp_call` items from `search-flight`
   and stores them via `kiwi_source.map_search_result()` + `db.insert_offers()`.
3. **Deterministic reporting** (`report.py`, after the loop ends) —
   `matching_offers()` queries SQLite directly (scoped by a `run_started_at`
   timestamp, not route string-matching, since Kiwi resolves city names
   like "Tokyo" to actual airport codes before we ever see them) and
   filters to whatever satisfies the trip's hard constraints (stops,
   minimum trip length, outbound date actually in range), `format_report()`
   builds the subject/body via plain string formatting, `deliver_report()`
   sends it via `email_sender.send_email()` (live — actually emails
   `RECIPIENT_EMAIL`) and also writes a local `output.txt` copy as a
   debug/audit trail. None of this involves the model — it runs in the
   loop's `finally` block regardless of how the run ended, so a report
   reflects whatever was found even on partial failure.

Loop mechanics (`run_agent.py`): hand-written, using the Responses API's
item-based input/output (not `previous_response_id` chaining). Per-turn,
`mcp_list_tools` items are dropped before resending (tools are declared
fresh via `tools=` every call regardless) and processed `mcp_call` outputs
are replaced with a short pointer once stored — otherwise the full raw
search JSON gets resent, and re-billed, on every subsequent turn. The loop
only stops once `total_search_calls >= MAX_SEARCH_CALLS` (the full plan is
done) — a turn with no search calls before then gets nudged to continue,
not treated as completion — with `MAX_ITERATIONS` as a hard backstop.
`max_output_tokens` / `max_tool_calls` are set directly on each request as
API-enforced structural caps, not just prompt nudges.

## Target user

Not a developer. Setup and running the tool is my job; my dad's only
interaction is receiving the email report. Error messages and setup steps
should be forgiving and clear regardless.

## Status

Built and working:
- `schema.sql` / `db.py` — canonical, source-agnostic SQLite persistence,
  plus an `agent_runs` table recording cost/behavior telemetry per run
- `kiwi_source.py` — Kiwi MCP tool-call builder + response mapper (verified
  against live data: exact field names, `USD` currency, multi-carrier legs)
- `report.py` — deterministic offer-filtering (stops, trip length, date
  range) + report formatting/delivery (no model involved)
- `email_sender.py` — Gmail SMTP send, live: `report.deliver_report()`
  actually emails `RECIPIENT_EMAIL` (credentials in `user-data.txt`), and
  also writes `output.txt` as a local debug/audit copy
- `run_agent.py` — the agent loop: MCP-only, with cost/rate-limit guardrails

Resolved constraint (kept for context — don't rediscover this the hard way
again): plain `gpt-4.1` capped at 30k tokens/minute on this account,
confirmed via `x-ratelimit-limit-tokens` headers — one *completed*
search-flight call costs ~14k input tokens (not the ~2.5k a too-output-
constrained/no-op call uses), so that tier could only do ~2 real searches
per minute regardless of pacing/retry strategy. Fixed by switching to
`gpt-4.1-mini` (200k tokens/minute on this account) rather than continuing
to work around the low tier. `SEARCH_PACING_SECONDS` still exists but is
now just a light courtesy pause (3s), not load-bearing.

Verified end-to-end (2026-07-13): full run against dad's real requirements
— 5/5 planned searches completed, genuine full-November coverage (spot-
checked: results span outbound dates across the whole month, not clustered
in a subset), 41 matching offers found, cheapest $2,217 for 2 people, real
email delivered, total cost $0.38.

Next: nothing blocking — the core pipeline works. Possible extensions if
revisited: SerpAPI as a second data source (cross-checked against Kiwi, not
just an emergency fallback — see the "Flight data source" note above on
why), the scheduled/autonomous operating mode, and turning the hardcoded
`ORIGIN`/`DESTINATION`/date-range/passenger constants in `run_agent.py`
into actual inputs rather than editing the file per search.
