
import unittest
from datetime import datetime, timedelta, timezone

from app.scoring import (
    MAX_SCORE,
    MIN_RUN_SECONDS,
    SEASON_EPOCH,
    ScoreError,
    beats,
    check_pace,
    check_region,
    check_score,
    decode,
    encode,
    neighbours,
    page_bounds,
    rank_board,
    run_seconds,
    validate_run,
    week_key,
)


class TestScoring(unittest.TestCase):

    def setUp(self):
        self.start = datetime(2026, 2, 1, 10, 0, tzinfo=timezone.utc)
        self.finish = self.start + timedelta(seconds=60)

    def make_run(self, **overrides):
        run = {
            "player": "Alice",
            "region": "north",
            "score": 1000,
            "run_id": "run-001",
            "started_at": self.start,
            "finished_at": self.finish,
        }
        run.update(overrides)
        return run

    # Score validation
    def test_valid_score(self):
        self.assertEqual(check_score(100), 100)

    def test_maximum_score_is_allowed(self):
        self.assertEqual(check_score(MAX_SCORE), MAX_SCORE)

    def test_negative_score_is_rejected(self):
        with self.assertRaises(ScoreError):
            check_score(-1)

    def test_score_above_maximum_is_rejected(self):
        with self.assertRaises(ScoreError):
            check_score(MAX_SCORE + 1)

    # Anti-cheat and run validation
    def test_valid_run(self):
        result = validate_run(self.make_run())
        self.assertEqual(result["player"], "Alice")
        self.assertEqual(result["seconds"], 60)

    def test_empty_player_is_rejected(self):
        with self.assertRaises(ScoreError):
            validate_run(self.make_run(player=" "))

    def test_duplicate_run_is_rejected(self):
        with self.assertRaises(ScoreError):
            validate_run(
                self.make_run(),
                seen_run_ids=["run-001"],
            )

    def test_run_too_short_is_rejected(self):
        with self.assertRaises(ScoreError):
            check_pace(100, MIN_RUN_SECONDS - 1)

    def test_impossible_scoring_speed_is_rejected(self):
        with self.assertRaises(ScoreError):
            check_pace(10001, 20)

    def test_finish_before_start_is_rejected(self):
        with self.assertRaises(ScoreError):
            run_seconds(self.finish, self.start)

    def test_invalid_region_is_rejected(self):
        with self.assertRaises(ScoreError):
            check_region("moon")

    # Composite score and tie-breaking
    def test_encode_decode_round_trip(self):
        finished = SEASON_EPOCH + timedelta(days=10, seconds=123)
        packed = encode(500, finished)
        score, decoded_time = decode(packed)

        self.assertEqual(score, 500)
        self.assertEqual(decoded_time, finished)

    def test_higher_score_ranks_higher(self):
        earlier = SEASON_EPOCH + timedelta(days=1)
        self.assertTrue(beats(encode(200, earlier),
                              encode(100, earlier)))

    def test_earlier_finish_wins_a_tie(self):
        earlier = SEASON_EPOCH + timedelta(seconds=100)
        later = SEASON_EPOCH + timedelta(seconds=200)

        self.assertTrue(beats(encode(500, earlier),
                              encode(500, later)))

    # Leaderboard ranking
    def test_equal_scores_share_competition_rank(self):
        earlier = SEASON_EPOCH + timedelta(seconds=100)
        later = SEASON_EPOCH + timedelta(seconds=200)

        board = rank_board([
            {"player": "Bob", "score": 900, "finished_at": later},
            {"player": "Alice", "score": 900, "finished_at": earlier},
            {"player": "Charlie", "score": 800, "finished_at": later},
        ])

        self.assertEqual(
            [entry["rank"] for entry in board],
            [1, 1, 3],
        )
        self.assertEqual(board[0]["player"], "Alice")

    # Weekly board and pagination
    def test_week_changes_on_monday(self):
        sunday = datetime(2026, 2, 1, 23, 59, tzinfo=timezone.utc)
        monday = datetime(2026, 2, 2, 0, 0, tzinfo=timezone.utc)

        self.assertNotEqual(week_key(sunday), week_key(monday))

    def test_page_bounds(self):
        self.assertEqual(page_bounds(2, 20), (20, 39))

    def test_invalid_page_size_is_rejected(self):
        with self.assertRaises(ScoreError):
            page_bounds(1, 0)

    def test_neighbours_at_first_rank(self):
        self.assertEqual(neighbours(1), (0, 2))


if __name__ == "__main__":
    unittest.main()
