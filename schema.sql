CREATE TABLE IF NOT EXISTS flight_offers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,

    -- when/how this row was captured
    observed_at TEXT NOT NULL,             -- ISO8601 timestamp, when this offer was fetched
    source TEXT NOT NULL,                  -- e.g. 'kiwi_mcp', 'serpapi'

    -- the date range this offer was found within (denormalized per-row,
    -- so no join is needed to know what a given search run was looking for)
    search_outbound_start TEXT NOT NULL,
    search_outbound_end TEXT NOT NULL,
    search_return_start TEXT,              -- NULL if the search was one-way
    search_return_end TEXT,

    -- route
    origin TEXT NOT NULL,                  -- IATA code, e.g. TLV
    destination TEXT NOT NULL,             -- IATA code, e.g. NRT / HND

    -- outbound leg
    outbound_departure TEXT NOT NULL,      -- ISO8601 datetime
    outbound_arrival TEXT NOT NULL,
    outbound_airline TEXT NOT NULL,
    outbound_stops INTEGER NOT NULL DEFAULT 0,
    outbound_layover_airports TEXT,        -- comma-separated IATA codes, NULL if direct

    -- return leg (all NULL if one-way)
    return_departure TEXT,
    return_arrival TEXT,
    return_airline TEXT,
    return_stops INTEGER,
    return_layover_airports TEXT,

    trip_type TEXT NOT NULL CHECK (trip_type IN ('one_way', 'round_trip')),

    -- price
    price REAL NOT NULL,
    currency TEXT NOT NULL,                -- e.g. 'USD', 'ILS'
    price_includes_return INTEGER NOT NULL DEFAULT 0,  -- 1 if `price` covers both legs

    cabin_class TEXT,                      -- economy / premium_economy / business / first
    passengers INTEGER NOT NULL DEFAULT 1,

    booking_url TEXT,                      -- deep link to book; may expire, treat alongside observed_at

    raw_json TEXT                          -- full raw offer from the source API, for debugging/backfill
);

CREATE INDEX IF NOT EXISTS idx_flight_offers_route_date
    ON flight_offers (origin, destination, outbound_departure);

CREATE INDEX IF NOT EXISTS idx_flight_offers_observed_at
    ON flight_offers (observed_at);

CREATE INDEX IF NOT EXISTS idx_flight_offers_price
    ON flight_offers (price);
