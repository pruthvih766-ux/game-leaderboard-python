from datetime import datetime, timedelta, timezone

import psycopg
from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse

from . import cache, db
from .scoring import (
    MAX_SCORE,
    REGIONS,
    ScoreError,
    check_region,
    decode,
    encode,
    neighbours,
    page_bounds,
    rank_board,
    validate_run,
    week_key,
    week_ttl_seconds,
)

app = FastAPI(title="game-leaderboard")

GLOBAL_KEY = "board:global"
READY_KEY = "board:ready"
CHUNK = 2000


def region_key(region):
    return "board:region:" + region


@app.get("/health")
def health():
    out = {"status": "ok", "postgres": False, "redis": False}
    try:
        db.query("SELECT 1")
        out["postgres"] = True
    except Exception as e:
        out["pg_error"] = str(e)
    try:
        cache.client().ping()
        out["redis"] = True
    except Exception as e:
        out["redis_error"] = str(e)
    return out if out["postgres"] and out["redis"] else JSONResponse(out, status_code=503)


def _chunked_zadd(pipe, key, mapping):
    items = list(mapping.items())
    for i in range(0, len(items), CHUNK):
        pipe.zadd(key, dict(items[i:i + CHUNK]))


def rebuild_boards():
    """Rebuild every sorted set from Postgres.

    Redis is a cache here, not the record. The seed fills Postgres only, and
    a cache that was flushed must be able to heal itself - so this runs
    lazily the first time a board is read, and can be forced from /admin.
    """
    r = cache.client()
    best = db.query(
        "SELECT DISTINCT ON (player) player, region, composite"
        " FROM runs ORDER BY player, composite DESC")
    now = datetime.now(timezone.utc)
    wk_key = week_key(now)
    weekly = db.query(
        "SELECT DISTINCT ON (player) player, composite FROM runs"
        " WHERE finished_at >= date_trunc('week', now())"
        " ORDER BY player, composite DESC")

    glob, by_region = {}, {}
    for row in best:
        glob[row["player"]] = float(row["composite"])
        by_region.setdefault(row["region"], {})[row["player"]] = float(row["composite"])

    pipe = r.pipeline()
    pipe.delete(GLOBAL_KEY, wk_key, *[region_key(x) for x in REGIONS])
    _chunked_zadd(pipe, GLOBAL_KEY, glob)
    for region, mapping in by_region.items():
        _chunked_zadd(pipe, region_key(region), mapping)
    _chunked_zadd(pipe, wk_key, {w["player"]: float(w["composite"]) for w in weekly})
    pipe.expire(wk_key, week_ttl_seconds(now))
    pipe.set(READY_KEY, now.isoformat())
    pipe.execute()
    return {"players": len(glob), "regions": len(by_region),
            "week_key": wk_key, "week_players": len(weekly)}


def ensure_boards():
    if not cache.client().exists(READY_KEY):
        rebuild_boards()


def board_key(scope, region):
    if scope == "global":
        return GLOBAL_KEY
    if scope == "week":
        return week_key(datetime.now(timezone.utc))
    if scope == "region":
        if not region:
            raise HTTPException(400, "scope=region needs a region")
        return region_key(check_region(region))
    raise HTTPException(400, "scope must be global, region or week")


def expand(entries):
    """Turn (player, composite) pairs back into something readable."""
    out = []
    for player, packed in entries:
        score, finished = decode(packed)
        out.append({"player": player, "score": score,
                    "finished_at": finished.isoformat()})
    return out


@app.post("/scores", status_code=201)
def submit(payload: dict = Body(...)):
    now = datetime.now(timezone.utc)
    try:
        seconds = int(payload.get("seconds", 0))
    except (TypeError, ValueError):
        raise HTTPException(400, "seconds must be a whole number")
    finished = now
    started = now - timedelta(seconds=max(seconds, 0))
    try:
        run = validate_run({**payload, "started_at": started, "finished_at": finished})
        packed = encode(run["score"], run["finished_at"])
    except ScoreError as e:
        raise HTTPException(400, str(e))
    except KeyError as e:
        raise HTTPException(400, "missing field %s" % e)

    try:
        with db.connect() as conn, conn.cursor() as cur:
            cur.execute("INSERT INTO players (name, region) VALUES (%s,%s)"
                        " ON CONFLICT (name) DO UPDATE SET region=EXCLUDED.region",
                        (run["player"], run["region"]))
            cur.execute(
                "INSERT INTO runs (run_id, player, region, score, seconds,"
                " finished_at, composite) VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING id",
                (run["run_id"], run["player"], run["region"], run["score"],
                 run["seconds"], run["finished_at"], float(packed)))
            row_id = cur.fetchone()["id"]
    except psycopg.errors.UniqueViolation:
        raise HTTPException(409, "run %s has already been submitted" % run["run_id"])

    ensure_boards()
    r = cache.client()
    wk = week_key(run["finished_at"])
    pipe = r.pipeline()
    # GT, so a bad run never costs a player the rank their best run earned.
    pipe.zadd(GLOBAL_KEY, {run["player"]: float(packed)}, gt=True)
    pipe.zadd(region_key(run["region"]), {run["player"]: float(packed)}, gt=True)
    pipe.zadd(wk, {run["player"]: float(packed)}, gt=True)
    pipe.expire(wk, week_ttl_seconds(run["finished_at"]))
    pipe.execute()

    rank = r.zrevrank(GLOBAL_KEY, run["player"])
    return {"id": row_id, "run_id": run["run_id"], "player": run["player"],
            "score": run["score"], "region": run["region"],
            "composite": packed, "global_rank": None if rank is None else rank + 1}


@app.get("/leaderboard")
def leaderboard(scope: str = Query("global"), region: str = Query(None),
                page: int = Query(1), size: int = Query(20)):
    ensure_boards()
    key = board_key(scope, region)
    try:
        start, stop = page_bounds(page, size)
    except ScoreError as e:
        raise HTTPException(400, str(e))
    r = cache.client()
    rows = r.zrevrange(key, start, stop, withscores=True)
    return {"scope": scope, "region": region, "key": key,
            "total_players": r.zcard(key), "page": page, "size": size,
            "tie_break": "equal scores: the earlier run ranks higher",
            "entries": [{**e, "rank": start + i + 1}
                        for i, e in enumerate(expand(rows))]}


@app.get("/players/{name}")
def player(name: str):
    ensure_boards()
    r = cache.client()
    rank = r.zrevrank(GLOBAL_KEY, name)
    if rank is None:
        raise HTTPException(404, "no scores on the board for %r" % name)

    row = db.one("SELECT region FROM players WHERE name=%s", (name,))
    region = row["region"] if row else None
    lo, hi = neighbours(rank + 1)
    around = expand(r.zrevrange(GLOBAL_KEY, lo, hi, withscores=True))
    for i, entry in enumerate(around):
        entry["rank"] = lo + i + 1
        entry["you"] = entry["player"] == name

    wk = week_key(datetime.now(timezone.utc))
    week_rank = r.zrevrank(wk, name)
    region_rank = r.zrevrank(region_key(region), name) if region else None
    best = db.query(
        "SELECT run_id, score, seconds, finished_at FROM runs WHERE player=%s"
        " ORDER BY composite DESC LIMIT 5", (name,))
    return {
        "player": name, "region": region,
        "global_rank": rank + 1, "global_players": r.zcard(GLOBAL_KEY),
        "region_rank": None if region_rank is None else region_rank + 1,
        "week_rank": None if week_rank is None else week_rank + 1,
        "neighbours": around, "best_runs": best,
    }


@app.get("/regions/{region}/podium")
def podium(region: str):
    """Top ten for a region, with competition ranks - so two players on the
    same score share a rank and the next one is pushed down."""
    ensure_boards()
    try:
        region = check_region(region)
    except ScoreError as e:
        raise HTTPException(400, str(e))
    rows = cache.client().zrevrange(region_key(region), 0, 9, withscores=True)
    if not rows:
        raise HTTPException(404, "no scores yet for %s" % region)
    return {"region": region, "podium": rank_board(
        [{"player": e["player"], "score": e["score"],
          "finished_at": datetime.fromisoformat(e["finished_at"])}
         for e in expand(rows)])}


@app.post("/admin/rebuild")
def rebuild():
    return {"rebuilt": True, **rebuild_boards(), "max_score": MAX_SCORE}
