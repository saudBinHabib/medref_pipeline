-- medref-pipeline initial schema (SPEC.md §3)
-- Applied via: psql "$DATABASE_URL" -f migrations/001_init.sql

CREATE TABLE IF NOT EXISTS manufacturers (
    manufacturer_id SERIAL PRIMARY KEY,
    name             TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS atc_reference (
    atc_code        TEXT PRIMARY KEY,
    atc_description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS medications (
    pzn                 TEXT PRIMARY KEY CHECK (pzn ~ '^[0-9]{8}$'),
    name                TEXT NOT NULL,
    active_ingredient   TEXT NOT NULL,
    dosage_form         TEXT NOT NULL CHECK (
                            dosage_form IN (
                                'tablet', 'capsule', 'solution',
                                'injection', 'cream', 'drops', 'spray'
                            )
                        ),
    strength             TEXT NOT NULL,
    prescription_only    BOOLEAN NOT NULL,
    price                 NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    manufacturer_id       INT REFERENCES manufacturers (manufacturer_id),
    atc_code              TEXT REFERENCES atc_reference (atc_code)
);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id         UUID PRIMARY KEY,
    source_file    TEXT NOT NULL,
    rows_in        INT,
    rows_out       INT,
    rows_rejected  INT,
    started_at     TIMESTAMPTZ NOT NULL,
    finished_at    TIMESTAMPTZ,
    status         TEXT NOT NULL CHECK (status IN ('running', 'success', 'failed')),
    content_hash   TEXT
);

-- Staging table mirrors `medications`; the load stage writes here first,
-- then atomically swaps/upserts into the serving table (SPEC.md §5.5).
CREATE TABLE IF NOT EXISTS medications_staging (
    pzn                 TEXT PRIMARY KEY CHECK (pzn ~ '^[0-9]{8}$'),
    name                TEXT NOT NULL,
    active_ingredient   TEXT NOT NULL,
    dosage_form         TEXT NOT NULL CHECK (
                            dosage_form IN (
                                'tablet', 'capsule', 'solution',
                                'injection', 'cream', 'drops', 'spray'
                            )
                        ),
    strength             TEXT NOT NULL,
    prescription_only    BOOLEAN NOT NULL,
    price                 NUMERIC(10, 2) NOT NULL CHECK (price >= 0),
    manufacturer_id       INT REFERENCES manufacturers (manufacturer_id),
    atc_code              TEXT REFERENCES atc_reference (atc_code)
);
