"""The flight-search agent. Given a date range (constants below for now),
searches Kiwi across it, stores every result in SQLite, and reports the
cheapest option found.

Architecture: the model's ONLY job is planning and running searches via the
Kiwi MCP tool. Everything after that — picking the cheapest offer,
formatting a report, delivering it — is plain code (report.py), not
something the model is asked to do. That's not just a cost decision:
computing MIN(price) and formatting known fields isn't a judgment call, so
there's no reason to spend a model turn on it.

Guardrails in this file (why they matter):
- MAX_ESTIMATED_COST_USD is a hard ceiling checked every turn, independent
  of iteration count — a single expensive turn can burn more than several
  cheap ones, so counting turns alone isn't a real cost guarantee.
- MAX_TOOL_CALLS_PER_TURN=1 is load-bearing, not just a nice-to-have: one
  real search-flight result costs roughly 11,000+ input tokens once it's
  actually executed (confirmed empirically — a call too output-constrained
  to complete a search used ~2,700 tokens; one that completed one used
  ~14,000). This account's gpt-4.1 tier caps at 30,000 tokens/minute, so
  letting the model attempt more than one search per turn can exceed the
  entire per-minute budget in a single request, regardless of any other
  guardrail here.
- Rate limits are handled *proactively*, not just reactively: SEARCH_PACING_SECONDS
  sleeps between every search turn, not just after a 429. A pure retry-after-failure
  approach was tried first and made things worse — evidence suggests failed 429
  attempts themselves consume/reserve quota, so retrying quickly compounds the
  problem instead of waiting it out. The retry logic is kept as a backstop for
  the pacing not being quite enough, not as the primary mechanism.
- Every run is recorded to the agent_runs table via a `finally` block, so
  cost is auditable even when a run errors out or hits a guardrail —
  that's exactly the case you most want a record of.
"""

import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from openai import APIError, OpenAI, RateLimitError

from db import init_db, insert_offers, record_run
from kiwi_source import map_search_result
from report import deliver_report, format_report, matching_offers

PROJECT_ROOT = Path(__file__).resolve().parent
load_dotenv(PROJECT_ROOT / "user-data.txt")

MODEL = "gpt-4.1-mini"  # non-reasoning model, deliberately: planning a few date-pair
                   # searches doesn't need multi-step logical reasoning, so a
                   # reasoning-tier model buys nothing here except hidden
                   # reasoning tokens billed at the expensive output rate.
                   # Switched from plain gpt-4.1 after confirming (via rate-limit
                   # header checks) that gpt-4.1 has an outlier-low 30k TPM limit
                   # on this account, vs. 200k for gpt-4.1-mini — mini is also
                   # cheaper per-token on top of that.
MAX_ITERATIONS = 30
MAX_ESTIMATED_COST_USD = 0.50  # hard cap: aborts the run if exceeded. Calibrated from real
                                # data: ~14k input tokens per completed search (~$0.006 each on
                                # gpt-4.1-mini) means MAX_SEARCH_CALLS=12 needs well under $0.10.
MAX_OUTPUT_TOKENS_PER_TURN = 300  # just needs to fit one tool call's worth of arguments
MAX_TOOL_CALLS_PER_TURN = 1        # load-bearing — see module docstring
MAX_RATE_LIMIT_RETRIES = 5
SEARCH_PACING_SECONDS = 3  # light pacing only — gpt-4.1-mini's 200k TPM limit comfortably
                            # fits ~14 real searches/minute, so this is just a courtesy pause,
                            # not a load-bearing rate-limit workaround like it was for gpt-4.1

# Verify against https://developers.openai.com/api/docs/pricing before
# trusting this for anything beyond a rough estimate — prices change, and
# this was current as of 2026-07-13. Keyed by the *resolved* model id
# (response.model), not the bare alias, since aliases can repoint over time.
PRICING_PER_MILLION = {
    "gpt-4.1": {"input": 2.00, "output": 8.00},
    "gpt-4.1-mini": {"input": 0.40, "output": 1.60},
    "gpt-5.6-sol": {"input": 5.00, "output": 30.00},
    "gpt-5.6-terra": {"input": 2.50, "output": 15.00},
    "gpt-5.6-luna": {"input": 1.00, "output": 6.00},
}

ORIGIN = "TLV"
DESTINATION = "Tokyo"
ADULTS = 2                # "2 tickets"
MIN_TRIP_NIGHTS = 21      # "minimum 3 weeks" — enforced against real search results in report.py,
                           # not just asked of the model, since a search's flex window can return
                           # itineraries that don't actually satisfy the pairing you intended.
MAX_STOPS = 1              # "not more than one switch" — Kiwi's search-flight tool has no stops
                           # filter, so this is a hard code-side filter, not something the model
                           # can search for directly.

OUTBOUND_RANGE_START = "2026-11-01"
OUTBOUND_RANGE_END = "2026-11-30"
FLEX_DAYS = 3  # matches Kiwi's max departureDateFlexDays/returnDateFlexDays

KIWI_MCP_TOOL = {
    "type": "mcp",
    "server_label": "kiwi-flights",
    "server_url": "https://mcp.kiwi.com",
    "require_approval": "never",
}


def _generate_search_plan(
    outbound_start: str, outbound_end: str, min_trip_nights: int, flex_days: int
) -> list[tuple[date, date]]:
    """Deterministically tile the outbound range into non-overlapping
    departure anchors (each anchor's +/-flex_days window covers 2*flex_days+1
    days), paired with a return anchor comfortably past the minimum trip
    length. This exists because leaving "how many searches, spaced how"
    to the model's own judgment was tried first and failed — it searched 3
    dates clustered in the middle of the month and called that "adequately
    covered," leaving both ends of the range never searched. Coverage is a
    correctness requirement, not a judgment call, so it's computed here.
    """
    start_d = date.fromisoformat(outbound_start)
    end_d = date.fromisoformat(outbound_end)
    step = timedelta(days=2 * flex_days)

    anchors = []
    current = start_d + timedelta(days=flex_days)
    while current - timedelta(days=flex_days) <= end_d:
        anchors.append(current)
        current += step
    last_needed = end_d - timedelta(days=flex_days)
    if not anchors or anchors[-1] < last_needed:
        anchors.append(max(last_needed, start_d + timedelta(days=flex_days)))

    # Return anchor sits at min_trip_nights + flex_days past departure, so the
    # return search window [return_anchor - flex_days, +flex_days] falls
    # entirely at or beyond the minimum — no wasted "too short" results.
    return [(dep, dep + timedelta(days=min_trip_nights + flex_days)) for dep in anchors]


SEARCH_PLAN = _generate_search_plan(OUTBOUND_RANGE_START, OUTBOUND_RANGE_END, MIN_TRIP_NIGHTS, FLEX_DAYS)
MAX_SEARCH_CALLS = len(SEARCH_PLAN)  # the plan IS the stop condition — see the loop's nudge logic

# Bookkeeping only (stored per-row for reference) — actual filtering happens in
# report.py against real per-itinerary dates, not these bounds.
RETURN_RANGE_START = (min(ret for _, ret in SEARCH_PLAN) - timedelta(days=FLEX_DAYS)).isoformat()
RETURN_RANGE_END = (max(ret for _, ret in SEARCH_PLAN) + timedelta(days=FLEX_DAYS)).isoformat()

_plan_lines = "\n".join(
    f"{i}. departureDate={dep.strftime('%d/%m/%Y')}, returnDate={ret.strftime('%d/%m/%Y')}"
    for i, (dep, ret) in enumerate(SEARCH_PLAN, start=1)
)

INSTRUCTIONS = f"""
You are running flight searches from {ORIGIN} to {DESTINATION} for {ADULTS} adults.

Call the search-flight tool exactly {MAX_SEARCH_CALLS} times — once per turn — using
EXACTLY these date pairs, in order. Every call: departureDateFlexDays={FLEX_DAYS},
returnDateFlexDays={FLEX_DAYS}, adults={ADULTS}, currency=USD.

{_plan_lines}

This list is fixed — don't skip any, don't add extra searches, don't stop
early. After all {MAX_SEARCH_CALLS} calls are done, stop — don't call any more
tools and don't write a summary. Filtering and reporting results is handled
separately; your only job is executing this exact list of searches.
""".strip()


def _compact_for_history(item):
    """Trim bulky items before they're resent as input on later turns —
    search-flight results are already persisted to SQLite by the time this
    runs, so the raw JSON doesn't need to keep being resent (and re-billed)
    turn after turn.
    """
    if item.type == "mcp_list_tools":
        return None  # tools are declared fresh via `tools=` every call anyway
    if item.type == "mcp_call" and item.name == "search-flight" and item.output:
        compact = item.model_dump()
        compact["output"] = (
            f"[{len(item.output)} chars of raw results omitted from history — already stored]"
        )
        return compact
    return item


def _lookup_pricing(model: str) -> dict | None:
    """Resolved model ids can carry a dated-snapshot suffix (e.g.
    'gpt-4.1-2025-04-14') that won't exact-match a bare alias key like
    'gpt-4.1' — this bit us once already (silently disabled the budget cap
    for a whole run). Fall back to a prefix match.
    """
    if model in PRICING_PER_MILLION:
        return PRICING_PER_MILLION[model]
    for key, pricing in PRICING_PER_MILLION.items():
        if model.startswith(key):
            return pricing
    return None


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    pricing = _lookup_pricing(model)
    if pricing is None:
        return 0.0  # unknown model — can't estimate; budget cap won't apply, see warning at call site
    return (input_tokens / 1_000_000) * pricing["input"] + (output_tokens / 1_000_000) * pricing["output"]


def run() -> None:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    conn = init_db(PROJECT_ROOT / "flights.db")
    run_started_at = datetime.now(timezone.utc).isoformat()

    input_list = [{"role": "user", "content": INSTRUCTIONS}]

    resolved_model = None
    total_input_tokens = 0
    total_output_tokens = 0
    total_search_calls = 0
    total_offers_stored = 0
    stop_reason = "max_iterations"
    iteration = 0

    try:
        for iteration in range(1, MAX_ITERATIONS + 1):
            print(f"\n--- Turn {iteration} ---")

            response = None
            for retry in range(MAX_RATE_LIMIT_RETRIES + 1):
                try:
                    response = client.responses.create(
                        model=MODEL,
                        tools=[KIWI_MCP_TOOL],
                        input=input_list,
                        max_output_tokens=MAX_OUTPUT_TOKENS_PER_TURN,
                        max_tool_calls=MAX_TOOL_CALLS_PER_TURN,
                    )
                    break
                except RateLimitError as e:
                    if retry >= MAX_RATE_LIMIT_RETRIES:
                        print(f"  rate limited {MAX_RATE_LIMIT_RETRIES} times in a row, giving up: {e}")
                        stop_reason = "api_error"
                        return
                    match = re.search(r"try again in ([\d.]+)s", str(e))
                    # Be more conservative than the API's own suggested wait — evidence
                    # suggests failed attempts also consume quota, so the true remaining
                    # wait is often longer than what a single error message reports.
                    wait_s = max(float(match.group(1)) + 5, SEARCH_PACING_SECONDS) if match else SEARCH_PACING_SECONDS
                    print(f"  rate limited — waiting {wait_s:.1f}s before retry {retry + 1}/{MAX_RATE_LIMIT_RETRIES}")
                    time.sleep(wait_s)
                except APIError as e:
                    print(f"  OpenAI API error: {e}")
                    stop_reason = "api_error"
                    return

            resolved_model = response.model
            total_input_tokens += response.usage.input_tokens
            total_output_tokens += response.usage.output_tokens
            cost_so_far = estimate_cost_usd(resolved_model, total_input_tokens, total_output_tokens)
            cached = response.usage.input_tokens_details.cached_tokens
            print(
                f"  this turn: {response.usage.input_tokens} in ({cached} cached) / "
                f"{response.usage.output_tokens} out"
            )
            print(f"  cumulative: {total_input_tokens} in / {total_output_tokens} out (~${cost_so_far:.4f} est.)")

            if _lookup_pricing(resolved_model) is None:
                print(f"  WARNING: no pricing entry for '{resolved_model}' — budget cap is NOT being enforced")
            elif cost_so_far >= MAX_ESTIMATED_COST_USD:
                print(f"  BUDGET CAP HIT (${cost_so_far:.4f} >= ${MAX_ESTIMATED_COST_USD}) — stopping the run")
                stop_reason = "budget_exceeded"
                return

            search_calls_this_turn = 0
            for item in response.output:
                if item.type == "mcp_call" and item.name == "search-flight":
                    if item.error:
                        print(f"  search-flight call errored: {item.error}")
                        continue
                    search_calls_this_turn += 1
                    total_search_calls += 1
                    rows = map_search_result(
                        item.output,
                        search_outbound_start=OUTBOUND_RANGE_START,
                        search_outbound_end=OUTBOUND_RANGE_END,
                        search_return_start=RETURN_RANGE_START,
                        search_return_end=RETURN_RANGE_END,
                        cabin_class=None,
                        adults=ADULTS,
                    )
                    inserted = insert_offers(conn, rows)
                    total_offers_stored += inserted
                    print(f"  stored {inserted} offers from a search-flight call")

            for item in response.output:
                compacted = _compact_for_history(item)
                if compacted is not None:
                    input_list.append(compacted)

            if total_search_calls >= MAX_SEARCH_CALLS:
                print(f"  completed all {MAX_SEARCH_CALLS} planned searches — stopping")
                stop_reason = "search_complete"
                return

            if search_calls_this_turn == 0:
                # The model stopped before finishing the plan — this is exactly what
                # happened with open-ended "adequately covered" instructions (stopped
                # after 3 of a needed ~5+). Nudge it to continue rather than accepting
                # an early stop; the plan, not the model's judgment, is the stop condition.
                remaining = MAX_SEARCH_CALLS - total_search_calls
                print(f"  model paused with {remaining} planned search(es) still remaining — nudging it to continue")
                input_list.append({
                    "role": "user",
                    "content": (
                        f"You have {remaining} more search(es) left from the list — "
                        "continue with the next one now."
                    ),
                })

            print(f"  pacing: waiting {SEARCH_PACING_SECONDS}s before the next search turn")
            time.sleep(SEARCH_PACING_SECONDS)

        print(f"\nStopped after {MAX_ITERATIONS} turns.")

    finally:
        offers = matching_offers(
            conn, run_started_at,
            max_stops=MAX_STOPS,
            min_trip_nights=MIN_TRIP_NIGHTS,
            outbound_not_before=OUTBOUND_RANGE_START,
            outbound_not_after=OUTBOUND_RANGE_END,
        )
        subject, body = format_report(offers, ORIGIN, DESTINATION)
        deliver_report(subject, body)
        report_delivered = bool(offers)

        finished_at = datetime.now(timezone.utc).isoformat()
        final_cost = estimate_cost_usd(resolved_model or MODEL, total_input_tokens, total_output_tokens)
        record_run(conn, {
            "started_at": run_started_at,
            "finished_at": finished_at,
            "model": resolved_model or MODEL,
            "iterations": iteration,
            "search_calls": total_search_calls,
            "offers_stored": total_offers_stored,
            "input_tokens": total_input_tokens,
            "output_tokens": total_output_tokens,
            "estimated_cost_usd": final_cost,
            "stop_reason": stop_reason,
            "report_delivered": int(report_delivered),
        })
        print(
            f"\n[run summary] {iteration} turns, {total_search_calls} searches, "
            f"{total_offers_stored} offers stored, {total_input_tokens}+{total_output_tokens} tokens, "
            f"~${final_cost:.4f}, stop_reason={stop_reason}, delivered={report_delivered}"
        )


if __name__ == "__main__":
    run()
