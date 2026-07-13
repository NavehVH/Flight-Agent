"""SQLite persistence for flight offers.

Deliberately source-agnostic: this module only knows about the canonical row
shape (the flight_offers columns). Each data source (kiwi_source.py, and
later e.g. serpapi_source.py) is responsible for mapping its own response
format into that shape. insert_offers() doesn't care where the rows came
from.
"""

import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"
DEFAULT_DB_PATH = Path(__file__).resolve().parent / "flights.db"

# Canonical row shape — must match flight_offers columns in schema.sql
# (excluding the autoincrement `id`).
OFFER_COLUMNS = [
    "observed_at",
    "source",
    "source_offer_id",
    "search_outbound_start",
    "search_outbound_end",
    "search_return_start",
    "search_return_end",
    "origin",
    "destination",
    "outbound_departure",
    "outbound_arrival",
    "outbound_airline",
    "outbound_stops",
    "outbound_layover_airports",
    "outbound_duration_seconds",
    "return_departure",
    "return_arrival",
    "return_airline",
    "return_stops",
    "return_layover_airports",
    "return_duration_seconds",
    "trip_type",
    "price",
    "currency",
    "price_includes_return",
    "cabin_class",
    "passengers",
    "booking_url",
    "raw_json",
]


def init_db(db_path: Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open (creating if needed) the SQLite db and ensure the schema exists."""
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    return conn


def insert_offers(conn: sqlite3.Connection, offers: list[dict]) -> int:
    """Insert canonical offer row dicts. Missing keys are stored as NULL.

    Every source's mapper produces dicts with a subset/all of OFFER_COLUMNS
    as keys — this function doesn't need to know which source they came
    from. Returns the number of rows inserted.
    """
    if not offers:
        return 0

    placeholders = ", ".join("?" for _ in OFFER_COLUMNS)
    sql = f"INSERT INTO flight_offers ({', '.join(OFFER_COLUMNS)}) VALUES ({placeholders})"

    rows = [tuple(offer.get(col) for col in OFFER_COLUMNS) for offer in offers]
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)
