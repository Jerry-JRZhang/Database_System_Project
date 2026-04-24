-- ============================================================
-- EquityDB schema (3NF / BCNF where reasonable)
-- ============================================================

-- ---------- Reference data ----------

CREATE TABLE IF NOT EXISTS exchange (
    exchange_id  SMALLSERIAL PRIMARY KEY,
    code         TEXT NOT NULL UNIQUE,        -- e.g. 'XNYS', 'XNAS'
    name         TEXT NOT NULL,
    country      TEXT NOT NULL DEFAULT 'US',
    tz           TEXT NOT NULL DEFAULT 'America/New_York'
);

CREATE TABLE IF NOT EXISTS sector (
    sector_id    SMALLSERIAL PRIMARY KEY,
    name         TEXT NOT NULL UNIQUE          -- GICS Sector
);

CREATE TABLE IF NOT EXISTS industry (
    industry_id  SERIAL PRIMARY KEY,
    sector_id    SMALLINT NOT NULL REFERENCES sector(sector_id),
    name         TEXT NOT NULL,                -- GICS Sub-Industry
    UNIQUE (sector_id, name)
);

CREATE TABLE IF NOT EXISTS ticker (
    ticker_id     SERIAL PRIMARY KEY,
    symbol        TEXT NOT NULL UNIQUE,
    name          TEXT NOT NULL,
    exchange_id   SMALLINT REFERENCES exchange(exchange_id),
    industry_id   INTEGER  REFERENCES industry(industry_id),
    cik           BIGINT,
    headquarters  TEXT,
    date_added    DATE,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE
);

-- Trading session metadata (per exchange, per date)
CREATE TABLE IF NOT EXISTS trading_calendar (
    exchange_id   SMALLINT NOT NULL REFERENCES exchange(exchange_id),
    session_date  DATE     NOT NULL,
    open_ts       TIMESTAMPTZ NOT NULL,
    close_ts      TIMESTAMPTZ NOT NULL,
    is_early_close BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (exchange_id, session_date)
);

-- ---------- Market data: 5-minute bars (partitioned) ----------

-- Parent table. PARTITION BY RANGE on ts. Children are created in 02_partitions.sql.
CREATE TABLE IF NOT EXISTS bar_5m (
    ticker_id    INTEGER     NOT NULL REFERENCES ticker(ticker_id),
    ts           TIMESTAMPTZ NOT NULL,
    open         DOUBLE PRECISION NOT NULL,
    high         DOUBLE PRECISION NOT NULL,
    low          DOUBLE PRECISION NOT NULL,
    close        DOUBLE PRECISION NOT NULL,
    volume       BIGINT           NOT NULL,
    vwap         DOUBLE PRECISION,
    trade_count  INTEGER,
    PRIMARY KEY (ticker_id, ts),
    CONSTRAINT bar_5m_ohlc_chk CHECK (high >= low AND high >= open AND high >= close
                                      AND low  <= open AND low  <= close),
    CONSTRAINT bar_5m_vol_chk  CHECK (volume >= 0)
) PARTITION BY RANGE (ts);
