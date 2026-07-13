"""Post-search reporting: filter to offers that actually satisfy the trip's
hard constraints and format/deliver the report. Plain code — deliberately
NOT an agent tool. The agent's only job is planning searches (via the Kiwi
MCP tool); filtering known fields against fixed rules and formatting them
isn't a judgment call, so there's no reason to pay for an LLM turn to do it.
"""

import sqlite3
from pathlib import Path

from email_sender import send_email

OUTPUT_FILE = Path(__file__).resolve().parent / "output.txt"


def matching_offers(
    conn: sqlite3.Connection,
    run_started_at: str,
    max_stops: int = 1,
    min_trip_nights: int = 21,
    outbound_not_before: str | None = None,
    outbound_not_after: str | None = None,
) -> list[dict]:
    """All offers stored since this run started that satisfy the hard trip
    constraints — at most `max_stops` per leg, at least `min_trip_nights`
    between outbound departure and return departure, and (if given) the
    outbound departure actually falling within [outbound_not_before,
    outbound_not_after].

    These are enforced here, in code, against the real per-itinerary dates
    Kiwi returned — not just requested of the model in the search
    instructions — because a search's flex window can return itineraries
    outside the intended window (e.g. a search anchored near a month
    boundary with +/-3 day flex can return dates from the adjacent month;
    this happened in practice and is why outbound_not_before/after exist).
    Kiwi's search-flight tool also has no stops filter at all, so max_stops
    has to be enforced somewhere, and code is more reliable than asking the
    model to self-police either constraint.

    Sorted cheapest-first, but not limited to a top-N — every offer that
    actually qualifies is included.
    """
    conditions = [
        "observed_at >= ?",
        "outbound_stops <= ?",
        "return_stops <= ?",
        "return_departure IS NOT NULL",
        "julianday(return_departure) - julianday(outbound_departure) >= ?",
    ]
    params: list = [run_started_at, max_stops, max_stops, min_trip_nights]

    if outbound_not_before is not None:
        conditions.append("outbound_departure >= ?")
        params.append(outbound_not_before)
    if outbound_not_after is not None:
        # Exclusive upper bound in day terms — a date-only string like
        # '2026-11-30' would otherwise exclude any timestamp later that day
        # (e.g. '2026-11-30T14:00:00' > '2026-11-30'). Compare by date only.
        conditions.append("date(outbound_departure) <= date(?)")
        params.append(outbound_not_after)

    sql = f"""
        SELECT price, currency, outbound_departure, outbound_arrival,
               outbound_airline, outbound_stops, return_departure,
               return_arrival, return_airline, return_stops, booking_url
        FROM flight_offers
        WHERE {' AND '.join(conditions)}
        ORDER BY price ASC
    """
    cur = conn.execute(sql, params)
    columns = [d[0] for d in cur.description]
    return [dict(zip(columns, row)) for row in cur.fetchall()]


def format_report(offers: list[dict], origin: str, destination: str) -> tuple[str, str]:
    """Build (subject, body) listing every matching offer. Plain string
    formatting, no LLM — there's no natural-language judgment needed to
    present a list of structured rows readably.
    """
    if not offers:
        return (
            f"No matching flights found: {origin} -> {destination}",
            "No flight offers matched the requirements (max 1 stop per leg, "
            "minimum trip length) in this search run. Check the run logs — "
            "either nothing was found, or everything found was filtered out.",
        )

    subject = (
        f"{len(offers)} matching {origin}–{destination} flights found "
        f"(cheapest: {offers[0]['price']:.0f} {offers[0]['currency']})"
    )

    lines = [
        f"{len(offers)} round-trip flights found: {origin} -> {destination}",
        "(sorted cheapest first)",
        "",
    ]
    for i, offer in enumerate(offers, start=1):
        lines.append(f"{i}. {offer['price']:.0f} {offer['currency']}")
        lines.append(
            f"   Outbound: {offer['outbound_departure']} -> {offer['outbound_arrival']} "
            f"({offer['outbound_airline']}, {offer['outbound_stops']} stop(s))"
        )
        lines.append(
            f"   Return:   {offer['return_departure']} -> {offer['return_arrival']} "
            f"({offer['return_airline']}, {offer['return_stops']} stop(s))"
        )
        lines.append(f"   Booking:  {offer['booking_url']}")
        lines.append("")

    return subject, "\n".join(lines)


def deliver_report(subject: str, body: str) -> None:
    """Send the report by real email, and also keep a local copy in
    output.txt + console — cheap to keep as a debug/audit trail even now
    that delivery is real.
    """
    report = f"Subject: {subject}\n\n{body}\n"
    OUTPUT_FILE.write_text(report)
    print("\n" + "=" * 60)
    print(report, end="")
    print("=" * 60)

    send_email(subject, body)
    print("Email sent.")
