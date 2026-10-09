
import json
import os
import unittest
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import quote


BASE_URL = os.getenv("TEST_BASE_URL", "http://localhost:8080")


class TestLeaderboardIntegration(unittest.TestCase):

    def api_request(self, path, method="GET", payload=None):
        data = None

        if payload is not None:
            data = json.dumps(payload).encode("utf-8")

        request = Request(
            BASE_URL + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )

        try:
            with urlopen(request, timeout=10) as response:
                return response.status, json.loads(
                    response.read().decode("utf-8")
                )
        except HTTPError as error:
            body = error.read().decode("utf-8")
            return error.code, json.loads(body)

    def make_payload(self):
        unique_id = uuid.uuid4().hex[:12]

        return {
            "player": "TestPlayer-" + unique_id,
            "region": "north",
            "score": 1000,
            "run_id": "test-run-" + unique_id,
            "seconds": 60,
        }

    def test_health_check(self):
        status, result = self.api_request("/health")

        self.assertEqual(status, 200)
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["postgres"])
        self.assertTrue(result["redis"])

    def test_valid_score_submission(self):
        status, result = self.api_request(
            "/scores", "POST", self.make_payload()
        )

        self.assertEqual(status, 201)
        self.assertEqual(result["score"], 1000)
        self.assertIn("global_rank", result)

    def test_duplicate_run_is_rejected(self):
        payload = self.make_payload()

        first_status, _ = self.api_request(
            "/scores", "POST", payload
        )
        second_status, _ = self.api_request(
            "/scores", "POST", payload
        )

        self.assertEqual(first_status, 201)
        self.assertEqual(second_status, 409)

    def test_score_above_maximum_is_rejected(self):
        payload = self.make_payload()
        payload["score"] = 1_000_001

        status, _ = self.api_request("/scores", "POST", payload)

        self.assertEqual(status, 400)

    def test_leaderboard_endpoint(self):
        status, result = self.api_request(
            "/leaderboard?scope=global&page=1&size=5"
        )

        self.assertEqual(status, 200)
        self.assertIn("total_players", result)
        self.assertIn("entries", result)
        self.assertIn("tie_break", result)

    def test_player_profile_after_submission(self):
        payload = self.make_payload()

        status, _ = self.api_request("/scores", "POST", payload)
        self.assertEqual(status, 201)

        player_url = "/players/" + quote(payload["player"], safe="")
        profile_status, profile = self.api_request(player_url)

        self.assertEqual(profile_status, 200)
        self.assertEqual(profile["player"], payload["player"])
        self.assertGreaterEqual(profile["global_rank"], 1)


if __name__ == "__main__":
    unittest.main()
