"""Verify isolated weekly delivery, cutoff correctness and no paper ledger writes."""
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from bitcoin_agent.config import load_config
from bitcoin_agent.integration_io import DeliveryError
from bitcoin_agent.notifications import DemoInbox
from bitcoin_agent.paper_engine import PaperEngine
from bitcoin_agent.paper_store import connection
from tools.cloud_gmail import cycle
from tools.install_cloud_gmail import compose_config, credentials, KEYS
from test_paper_engine import quote, features

ROOT = Path(__file__).resolve().parents[1]
MONDAY = datetime(2026, 11, 2, 6, tzinfo=timezone.utc)


class CloudGmailTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "paper"
        self.runtime.mkdir()
        self.state = self.root / "mail"
        self.manifest = {"run_id": "a" * 32, "fingerprint": {"frozen": True}}
        (self.runtime / "cloud-manifest.json").write_text(json.dumps(self.manifest))
        self.engine = PaperEngine(self.runtime / "ledger.sqlite3", load_config(ROOT / "config/demo.json"))
        self.sender = DemoInbox(self.root / "inbox")
        self.patch = patch("tools.cloud_gmail.fingerprint", return_value={"frozen": True})
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def tick(self, when, price="100"):
        self.engine.tick(quote(when, price), features(when), when)

    def ledger_hash(self):
        return hashlib.sha256((self.runtime / "ledger.sqlite3").read_bytes()).hexdigest()

    def test_before_boundary_no_send_then_exact_monday_once_and_restart(self):
        self.tick(MONDAY - timedelta(days=1))
        before = self.ledger_hash()
        self.assertEqual(cycle(self.runtime, self.state, MONDAY - timedelta(seconds=1), self.sender)["outbox"], [])
        first = cycle(self.runtime, self.state, MONDAY, self.sender)
        self.assertEqual(first["outbox"][0]["status"], "delivered")
        self.assertFalse(first["seven_day_validation_certified"])
        self.assertEqual(cycle(self.runtime, self.state, MONDAY + timedelta(hours=1), self.sender)["delivery_results"], [])
        self.assertEqual(len(list((self.root / "inbox").glob("*.json"))), 1)
        self.assertEqual(self.ledger_hash(), before)
        with connection(self.runtime / "ledger.sqlite3") as db:
            self.assertFalse(db.execute("SELECT 1 FROM sqlite_master WHERE name='notification_outbox'").fetchone())

    def test_catchup_does_not_include_post_boundary_fills(self):
        self.tick(MONDAY - timedelta(days=1))
        self.tick(MONDAY, "105")
        self.tick(MONDAY + timedelta(days=1), "90")
        cycle(self.runtime, self.state, MONDAY + timedelta(days=1), self.sender)
        payload = json.loads(next((self.root / "inbox").glob("*.json")).read_text())
        self.assertEqual(payload["metrics"]["period_end"], MONDAY.isoformat())
        self.assertEqual(payload["metrics"]["closing_mark_at"], MONDAY.isoformat())
        self.assertIn("Partial period", payload["metrics"]["coverage"])

    def test_no_history_or_start_after_boundary_waits(self):
        self.assertFalse(cycle(self.runtime, self.state, MONDAY, self.sender)["latest_report_eligible"])
        self.tick(MONDAY + timedelta(minutes=1))
        self.assertEqual(cycle(self.runtime, self.state, MONDAY + timedelta(hours=1), self.sender)["outbox"], [])

    def test_run_change_and_paper_state_overlap_rejected(self):
        cycle(self.runtime, self.state, MONDAY, self.sender)
        self.manifest["run_id"] = "b" * 32
        (self.runtime / "cloud-manifest.json").write_text(json.dumps(self.manifest))
        with self.assertRaises(ValueError):
            cycle(self.runtime, self.state, MONDAY, self.sender)
        with self.assertRaises(ValueError):
            cycle(self.runtime, self.runtime / "gmail", MONDAY, self.sender)

    def test_ambiguous_email_is_not_retried_after_restart(self):
        self.tick(MONDAY - timedelta(days=1))
        class Unknown:
            local = False
            def send(self, *args):
                raise DeliveryError("network_failure", ambiguous=True)
        self.assertTrue(cycle(self.runtime, self.state, MONDAY, Unknown())["needs_attention"])
        self.assertEqual(cycle(self.runtime, self.state, MONDAY + timedelta(minutes=10), self.sender)["delivery_results"], [])
        self.assertFalse((self.root / "inbox").exists())

    def test_server_connection_test_is_idempotent(self):
        cycle(self.runtime, self.state, MONDAY - timedelta(days=1), self.sender, test=True)
        cycle(self.runtime, self.state, MONDAY - timedelta(days=1), self.sender, test=True)
        self.assertEqual(len(list((self.root / "inbox").glob("*.json"))), 1)

    def test_deployment_only_mounts_paper_readonly_and_image_is_pinned(self):
        config = compose_config("sha256:" + "a" * 64, "bitcoin-evaluation_cloud_paper")
        self.assertEqual(set(config["services"]), {"mailer"})
        service = config["services"]["mailer"]
        self.assertEqual(service["image"], "sha256:" + "a" * 64)
        self.assertIn("paper_evidence:/app/evidence:ro", service["volumes"])
        self.assertEqual(service["pull_policy"], "never")
        self.assertNotIn("build", service)
        self.assertNotIn("ports", service)

    def test_credential_import_refuses_extra_secrets_and_newline_injection(self):
        values = dict(zip(KEYS, ["demo.apps.googleusercontent.com", "secret", "refresh", "owner@example.com"]))
        self.assertEqual(credentials(json.dumps(values)), values)
        with self.assertRaises(ValueError):
            credentials(json.dumps({**values, "TELEGRAM_BOT_TOKEN": "private"}))
        with self.assertRaises(ValueError):
            credentials(json.dumps({**values, "GMAIL_REFRESH_TOKEN": "refresh\nINJECT=1"}))


if __name__ == "__main__":
    unittest.main()
