"""Kiwi.com MCP flight search: build tool-call arguments and map its
response into the canonical row shape used by db.insert_offers().

This is the only file that knows Kiwi's specific field names (flyFrom,
departureTime, route, etc). A future second source (e.g. SerpAPI) gets its
own sibling module that maps into the same canonical shape — db.py and the
agent loop never need to know the difference.
"""

import json
from datetime import datetime, timezone

SOURCE_NAME = "kiwi_mcp"


def _to_kiwi_date(iso_date: str) -> str:
    """Convert 'YYYY-MM-DD' to Kiwi's required 'dd/mm/yyyy'."""
    return datetime.strptime(iso_date, "%Y-%m-%d").strftime("%d/%m/%Y")


def build_tool_input(
    origin: str,
    destination: str,
    departure_date: str,  # 'YYYY-MM-DD'
    return_date: str | None = None,  # 'YYYY-MM-DD', or None for one-way
    departure_flex_days: int = 0,
    return_flex_days: int = 0,
    adults: int = 1,
    children: int = 0,
    infants: int = 0,
    cabin_class: str | None = None,  # 'M' economy | 'W' premium econ | 'C' business | 'F' first
) -> dict:
    """Build the arguments dict for the search-flight MCP tool call."""
    args = {
        "flyFrom": origin,
        "flyTo": destination,
        "departureDate": _to_kiwi_date(departure_date),
        "departureDateFlexDays": departure_flex_days,
        "adults": adults,
        "children": children,
        "infants": infants,
        "currency": "USD",
    }
    if return_date:
        args["returnDate"] = _to_kiwi_date(return_date)
        args["returnDateFlexDays"] = return_flex_days
    if cabin_class:
        args["cabinClass"] = cabin_class
    return args


def _leg_airlines(leg: dict) -> str:
    """Comma-separated carrier codes across a leg's segments, de-duplicated
    but order-preserved (a leg with 3 segments on the same carrier stays a
    single code; a carrier change shows as e.g. 'LY,VF,D7')."""
    carriers = [seg["carrier"] for seg in leg.get("segments", [])]
    return ",".join(dict.fromkeys(carriers))


def _leg_layovers(leg: dict) -> str | None:
    """Intermediate airports only — route minus the first (origin) and last
    (destination) entries, which are already their own columns."""
    route = leg.get("route", [])
    intermediate = route[1:-1]
    return ",".join(intermediate) if intermediate else None


def map_search_result(
    raw_output: str,
    *,
    search_outbound_start: str,
    search_outbound_end: str,
    search_return_start: str | None,
    search_return_end: str | None,
    cabin_class: str | None,
    adults: int,
) -> list[dict]:
    """Parse the search-flight tool's raw JSON string output into a list of
    canonical row dicts ready for db.insert_offers().
    """
    data = json.loads(raw_output)
    currency = data.get("currency", "USD")
    trip_type = "round_trip" if search_return_start else "one_way"
    observed_at = datetime.now(timezone.utc).isoformat()

    rows = []
    for itinerary in data.get("itineraries", []):
        outbound = itinerary["outbound"]
        inbound = itinerary.get("inbound")

        rows.append({
            "observed_at": observed_at,
            "source": SOURCE_NAME,
            "source_offer_id": itinerary.get("id"),
            "search_outbound_start": search_outbound_start,
            "search_outbound_end": search_outbound_end,
            "search_return_start": search_return_start,
            "search_return_end": search_return_end,
            "origin": outbound["from"],
            "destination": outbound["to"],
            "outbound_departure": outbound["departureTime"],
            "outbound_arrival": outbound["arrivalTime"],
            "outbound_airline": _leg_airlines(outbound),
            "outbound_stops": outbound.get("stops", 0),
            "outbound_layover_airports": _leg_layovers(outbound),
            "outbound_duration_seconds": outbound.get("durationSeconds"),
            "return_departure": inbound["departureTime"] if inbound else None,
            "return_arrival": inbound["arrivalTime"] if inbound else None,
            "return_airline": _leg_airlines(inbound) if inbound else None,
            "return_stops": inbound.get("stops") if inbound else None,
            "return_layover_airports": _leg_layovers(inbound) if inbound else None,
            "return_duration_seconds": inbound.get("durationSeconds") if inbound else None,
            "trip_type": trip_type,
            "price": itinerary["price"],
            "currency": currency,
            "price_includes_return": 1 if trip_type == "round_trip" else 0,
            "cabin_class": cabin_class,
            "passengers": adults,
            "booking_url": itinerary.get("bookingUrl"),
            "raw_json": json.dumps(itinerary),
        })
    return rows
