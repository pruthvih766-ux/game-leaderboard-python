"""Pure scoring, anti-cheat and tie-breaking rules. No database, no HTTP.

Everything here is a plain function over plain data, so all of it is unit
testable without a container running.

What is worth testing
---------------------
* ``validate_run`` - a score above MAX_SCORE, a negative score, a run shorter
  than MIN_RUN_SECONDS, a run that scores faster than MAX_POINTS_PER_SECOND,
  a run whose end is before its start, a run submitted twice.
* ``encode``/``decode`` - round trip for a lot of (score, elapsed) pairs, and
  the boundary: the encoded value must stay below 2**53 or a Redis sorted set
  (which stores doubles) silently loses precision.
* ``encode`` ordering - a higher score always encodes higher; equal scores
  encode so the EARLIER run is higher. That is the tie-break, and it is the
  whole point of this module.
* ``rank_board`` - competition ranking, so two players on the same score share
  a rank and the next player is pushed down (1, 2, 2, 4 - not 1, 2, 2, 3).
* ``week_key`` - a run on Sunday night and a run on Monday morning land in
  different weeks. Check the year boundary too.
"""
from datetime import datetime, timedelta, timezone

# Nothing in this game can score more than this. A submission above it is
# either a bug or a cheat; either way it does not belong on the board.
MAX_SCORE = 1_000_000
MIN_SCORE = 0

# A run shorter than this did not happen.
MIN_RUN_SECONDS = 20
# Nor did a run that scored faster than a human can play.
MAX_POINTS_PER_SECOND = 500
# And nobody plays a single run for longer than this.
MAX_RUN_SECONDS = 4 * 60 * 60

# The tie-break packs two numbers into one sorted-set score. 32 bits is
# enough for 136 years of seconds, and MAX_SCORE * 2**32 is comfortably
# under 2**53, which is where a double stops counting whole numbers.
TIME_BITS = 32
TIME_SPAN = 1 << TIME_BITS

# Runs are timed from this instant. Changing it invalidates every stored
# composite score, so it is a constant, not a setting.
SEASON_EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)

REGIONS = ("north", "south", "east", "west", "central")


class ScoreError(ValueError):
    """A submission that must be rejected, with a reason a human can read."""


def _aware(ts):
    """Treat a naive timestamp as UTC rather than guessing or crashing."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


def elapsed_since_epoch(finished_at):
    """Whole seconds from SEASON_EPOCH to this instant."""
    secs = int((_aware(finished_at) - SEASON_EPOCH).total_seconds())
    if secs < 0:
        raise ScoreError("that run finished before the season started")
    if secs >= TIME_SPAN:
        raise ScoreError("that run is beyond the end of the season clock")
    return secs


def run_seconds(started_at, finished_at):
    """How long the run took, rejecting a run that ends before it begins."""
    secs = int((_aware(finished_at) - _aware(started_at)).total_seconds())
    if secs < 0:
        raise ScoreError("a run cannot finish before it started")
    return secs


def check_score(score):
    """A score has to be a whole number inside the possible range."""
    if isinstance(score, bool) or not isinstance(score, int):
        try:
            if float(score) != int(float(score)):
                raise ScoreError("a score must be a whole number")
            score = int(float(score))
        except (TypeError, ValueError):
            raise ScoreError("%r is not a score" % (score,))
    if score < MIN_SCORE:
        raise ScoreError("a score cannot be negative")
    if score > MAX_SCORE:
        raise ScoreError("%d is above the maximum possible score of %d"
                         % (score, MAX_SCORE))
    return score


def check_pace(score, seconds):
    """Reject a score that no amount of skill could reach in the time given.

    This is the cheat that matters: the client reports both the score and the
    duration, so a tampered client reports a huge score for a two second run.
    """
    if seconds < MIN_RUN_SECONDS:
        raise ScoreError("a run of %ds is too short to be real (minimum %ds)"
                         % (seconds, MIN_RUN_SECONDS))
    if seconds > MAX_RUN_SECONDS:
        raise ScoreError("a run of %ds is longer than anyone plays (maximum %ds)"
                         % (seconds, MAX_RUN_SECONDS))
    if score > seconds * MAX_POINTS_PER_SECOND:
        raise ScoreError("%d points in %ds beats the maximum rate of %d/s"
                         % (score, seconds, MAX_POINTS_PER_SECOND))
    return True


def check_region(region):
    r = str(region or "").strip().lower()
    if r not in REGIONS:
        raise ScoreError("%r is not a region (expected one of %s)"
                         % (region, ", ".join(REGIONS)))
    return r


def validate_run(run, seen_run_ids=()):
    """Full gate for one submission. Returns a cleaned, normalised dict.

    ``run`` carries player, region, score, run_id, started_at, finished_at.
    ``seen_run_ids`` is whatever the caller already knows about - the same
    run submitted twice is a duplicate, not a second score.
    """
    player = str(run.get("player", "")).strip()
    if not player:
        raise ScoreError("a submission needs a player")
    run_id = str(run.get("run_id", "")).strip()
    if not run_id:
        raise ScoreError("a submission needs a run_id so a replay can be spotted")
    if run_id in set(seen_run_ids):
        raise ScoreError("run %s has already been submitted" % run_id)

    region = check_region(run.get("region"))
    score = check_score(run.get("score"))
    started = _aware(run["started_at"])
    finished = _aware(run["finished_at"])
    secs = run_seconds(started, finished)
    check_pace(score, secs)

    return {"player": player, "region": region, "score": score,
            "run_id": run_id, "started_at": started, "finished_at": finished,
            "seconds": secs}


def encode(score, finished_at):
    """Pack a score and a finish time into one sortable number.

    Two players on the same score need a stable, defensible order, and
    "whoever the database happened to return first" is neither. The rule
    here is: same score, the player who got there EARLIER ranks higher.

    The score goes in the high bits so it always dominates. The low bits
    hold the time, inverted, so a smaller timestamp produces a bigger
    composite. The result is exact in a double, which is what a Redis
    sorted set stores.
    """
    score = check_score(score)
    secs = elapsed_since_epoch(finished_at)
    packed = score * TIME_SPAN + (TIME_SPAN - 1 - secs)
    if packed >= (1 << 53):
        raise ScoreError("composite score has outgrown double precision")
    return packed


def decode(packed):
    """Recover (score, finished_at) from a composite score."""
    packed = int(packed)
    if packed < 0:
        raise ScoreError("a composite score cannot be negative")
    score, low = divmod(packed, TIME_SPAN)
    secs = TIME_SPAN - 1 - low
    return score, SEASON_EPOCH + timedelta(seconds=secs)


def beats(a, b):
    """Does composite score ``a`` outrank composite score ``b``?"""
    return int(a) > int(b)


def rank_board(entries):
    """Order a board and assign competition ranks.

    ``entries`` is [{"player":..., "score":..., "finished_at":...}, ...].
    Equal scores share a rank and the next player is pushed down, so three
    players tied at the top are all rank 1 and the fourth is rank 4.
    Within a tied rank the earlier finisher is listed first; if two runs
    finished in the same second, the player name breaks it so the order
    never changes between page loads.
    """
    ordered = sorted(
        entries,
        key=lambda e: (-int(e["score"]),
                       elapsed_since_epoch(e["finished_at"]),
                       str(e["player"])))
    out = []
    last_score = None
    last_rank = 0
    for i, e in enumerate(ordered, start=1):
        score = int(e["score"])
        if score == last_score:
            rank = last_rank
        else:
            rank = i
            last_score, last_rank = score, i
        out.append({"rank": rank, "player": str(e["player"]), "score": score,
                    "finished_at": _aware(e["finished_at"]).isoformat()})
    return out


def week_key(when, prefix="board:week"):
    """Redis key for the weekly board this instant belongs to.

    ISO weeks, so the board rolls over at Monday 00:00 UTC and a run at
    Sunday 23:59 is on the old board.
    """
    y, w, _ = _aware(when).isocalendar()
    return "%s:%04d-W%02d" % (prefix, y, w)


def week_ttl_seconds(when, keep_weeks=3):
    """How long a weekly board should live: to the end of its week, plus a
    grace period so last week is still readable while results are checked."""
    d = _aware(when)
    _, _, weekday = d.isocalendar()
    monday = (d - timedelta(days=weekday - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    end_of_week = monday + timedelta(days=7)
    return int((end_of_week - d).total_seconds()) + keep_weeks * 7 * 86400


def page_bounds(page, size, max_size=100):
    """Translate a page number into inclusive zset indexes."""
    try:
        page = max(1, int(page))
        size = int(size)
    except (TypeError, ValueError):
        raise ScoreError("page and size must be whole numbers")
    if size < 1 or size > max_size:
        raise ScoreError("size must be between 1 and %d" % max_size)
    start = (page - 1) * size
    return start, start + size - 1


def neighbours(rank, span=2):
    """Inclusive zset indexes for a slice centred on a player's own rank.

    Rank 40,000 should see 39,998 to 40,002, and rank 1 must not produce a
    negative index (Redis would read that as counting from the end).
    """
    if rank < 1:
        raise ScoreError("ranks start at 1")
    idx = rank - 1
    return max(0, idx - span), idx + span
