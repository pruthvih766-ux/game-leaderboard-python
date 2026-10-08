CREATE TABLE IF NOT EXISTS players (
    name TEXT PRIMARY KEY,
    region TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now());

CREATE TABLE IF NOT EXISTS runs (
    id BIGSERIAL PRIMARY KEY,
    run_id TEXT UNIQUE NOT NULL,
    player TEXT NOT NULL REFERENCES players(name) ON DELETE CASCADE,
    region TEXT NOT NULL,
    score INT NOT NULL CHECK (score >= 0 AND score <= 1000000),
    seconds INT NOT NULL CHECK (seconds > 0),
    finished_at TIMESTAMPTZ NOT NULL,
    -- score and finish time packed into one sortable number; see app/scoring.py
    composite DOUBLE PRECISION NOT NULL);

CREATE INDEX IF NOT EXISTS runs_player_best ON runs (player, composite DESC);
CREATE INDEX IF NOT EXISTS runs_region_best ON runs (region, composite DESC);
CREATE INDEX IF NOT EXISTS runs_finished ON runs (finished_at DESC);
