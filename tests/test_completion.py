"""Publication gates reject incomplete or altered experiments."""

import json
import io
import unittest
from contextlib import redirect_stderr
from unittest.mock import patch

from tools.finalize_project import completion_checks
from tools.publish_project import main as publisher_main, publish


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.summary = {
            "seven_day_evidence_complete": True, "status": "seven_day_window_closed",
            "observer_failures": 0, "changed_config_checksums": [],
            "manifest": {"minimum_daily_quote_coverage": .99, "code_checksum": "code", "config_checksum": "config",
                         "environment": {"python": "fixture"}, "source": "coinbase_public_stream",
                         "historical_candles_count_as_forward": False},
            "days": [{"complete_24_hours": True, "qualifies": True, "quote_coverage_fraction": 1} for _ in range(7)]}
        self.audit = {"passed": True, "baseline_books_reconciled": 3, "candidate": {"passed": True}}
        self.context = {"paired": True, "code": "code", "config": "config", "environment": {"python": "fixture"}, "stopped": True}

    def checks(self):
        return completion_checks(self.summary, self.audit, **self.context)

    def test_complete_consistent_fixture_passes(self):
        self.assertTrue(all(self.checks().values()))

    def test_calendar_week_without_qualifying_coverage_cannot_finalize(self):
        self.summary["days"][2]["quote_coverage_fraction"] = .5
        self.assertFalse(all(self.checks().values()))

    def test_every_integrity_constraint_is_independent_of_time(self):
        for field, value in [("paired", False), ("code", "changed"), ("config", "changed"),
                             ("environment", {"python": "changed"}), ("stopped", False)]:
            with self.subTest(field=field):
                context = {**self.context, field: value}
                self.assertFalse(all(completion_checks(self.summary, self.audit, **context).values()))

    def test_observer_incident_and_unreconciled_book_block_finalization(self):
        self.summary["observer_failures"] = 1
        self.assertFalse(all(self.checks().values()))
        self.summary["observer_failures"] = 0
        self.audit["baseline_books_reconciled"] = 2
        self.assertFalse(all(self.checks().values()))

    def test_revoked_upload_authorization_prevents_any_remote_or_git_write(self):
        settings = json.dumps({"repository": "Abdulrahmanos/qytbcf1pULIvwxUO", "uploads_authorized": False})
        with patch("tools.publish_project.api") as api, patch("tools.publish_project.git") as git, \
                patch("tools.publish_project.Path.read_text", return_value=settings):
            with self.assertRaisesRegex(ValueError, "Uploads are disabled"):
                publish("fixture-not-a-credential")
        api.assert_not_called()
        git.assert_not_called()

    def test_upload_prohibition_is_checked_before_credential_access(self):
        settings = json.dumps({"uploads_authorized": False})
        with patch("sys.argv", ["publish_project.py", "--publish"]), \
                patch("tools.publish_project.Path.read_text", return_value=settings), \
                patch("tools.publish_project.credential") as credential, \
                patch("tools.publish_project.api") as api, redirect_stderr(io.StringIO()):
            self.assertEqual(publisher_main(), 3)
        credential.assert_not_called()
        api.assert_not_called()


if __name__ == "__main__":
    unittest.main()
