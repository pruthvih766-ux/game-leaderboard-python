-- Fifty thousand players, so "what is my rank when I am 40,000th?" is a
-- real question and not a toy one.
INSERT INTO players (name, region)
SELECT 'player_' || lpad(g::text, 5, '0'),
       (ARRAY['north','south','east','west','central'])[1 + (g % 5)]
FROM generate_series(1, 50000) AS g
ON CONFLICT (name) DO NOTHING;

-- Five named players at the sharp end, including a deliberate three-way tie
-- on 987654 so the tie-break shows up on the very first page of the board.
INSERT INTO players (name, region) VALUES
 ('asha','north'), ('vikram','south'), ('priya','east'),
 ('rahul','west'), ('fatima','central')
ON CONFLICT (name) DO NOTHING;

INSERT INTO runs (run_id, player, region, score, seconds, finished_at, composite)
SELECT r.run_id, r.player, r.region, r.score, r.seconds, r.finished_at,
       (r.score::numeric * 4294967296
        + (4294967295 - EXTRACT(EPOCH FROM (r.finished_at - TIMESTAMPTZ '2026-01-01 00:00:00+00'))::bigint)
       )::double precision
FROM (
    SELECT 'seed-' || g                                   AS run_id,
           'player_' || lpad(g::text, 5, '0')             AS player,
           (ARRAY['north','south','east','west','central'])[1 + (g % 5)] AS region,
           LEAST(1000000, (30 + (g * 7919) % 900000))::int AS score,
           (60 + (g % 1200))::int                          AS seconds,
           now() - ((g % 9) * INTERVAL '1 day')
                 - ((g % 23) * INTERVAL '1 hour')          AS finished_at
    FROM generate_series(1, 50000) AS g
) AS r
ON CONFLICT (run_id) DO NOTHING;

-- The tie: same score to the point, three different finish times. The
-- earliest finisher must come first, every time the page is loaded.
INSERT INTO runs (run_id, player, region, score, seconds, finished_at, composite)
SELECT t.run_id, t.player, t.region, t.score, t.seconds, t.finished_at,
       (t.score::numeric * 4294967296
        + (4294967295 - EXTRACT(EPOCH FROM (t.finished_at - TIMESTAMPTZ '2026-01-01 00:00:00+00'))::bigint)
       )::double precision
FROM (VALUES
  ('tie-asha',   'asha',   'north',   987654, 2100, now() - INTERVAL '3 hours'),
  ('tie-vikram', 'vikram', 'south',   987654, 2100, now() - INTERVAL '2 hours'),
  ('tie-priya',  'priya',  'east',    987654, 2100, now() - INTERVAL '1 hour'),
  ('top-rahul',  'rahul',  'west',    999001, 2400, now() - INTERVAL '5 hours'),
  ('mid-fatima', 'fatima', 'central', 400000, 1500, now() - INTERVAL '8 hours')
) AS t(run_id, player, region, score, seconds, finished_at)
ON CONFLICT (run_id) DO NOTHING;
