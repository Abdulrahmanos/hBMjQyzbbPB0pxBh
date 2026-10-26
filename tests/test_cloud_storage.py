"""Storage recovery preserves frozen accounts, fills, and future collection."""
import json
import sqlite3
import tempfile
import threading
import time
import unittest
from contextlib import closing
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from bitcoin_agent.config import load_config
from bitcoin_agent.forward import ForwardEvaluation, audit_forward, forward_summary
from bitcoin_agent.paper_engine import PaperEngine
from bitcoin_agent.paper_store import connection, paper_status
from tools import cloud_evaluation as cloud
from tools import repair_cloud_storage as storage
from test_paper_engine import START, features, quote


class StorageRecoveryTests(unittest.TestCase):
    def setUp(self):
        base = cloud.ROOT / "var/test-temp"
        base.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.runtime = self.root / "paper"
        self.output = self.root / "maintenance"
        self.runtime.mkdir()
        self.path = self.runtime / "ledger.sqlite3"
        self.config = load_config(cloud.CONFIG)
        cloud.freeze(self.runtime)
        self.engine = PaperEngine(self.path, self.config)
        self.evaluation = ForwardEvaluation(self.path, self.config)
        self.tick(START)

    def tick(self, now):
        q, f = quote(now), features(now)
        result = self.engine.tick(q, f, now)
        self.evaluation.observe(q, f, result, now, self.config, self.engine.rules)

    def test_repair_preserves_account_fills_and_frozen_files(self):
        before = paper_status(self.path)
        identity = storage.account_identity(self.path)
        frozen = (self.runtime / "cloud-manifest.json").read_bytes()
        self.assertEqual(storage.journal_mode(self.path), "wal")
        result = storage.repair(self.runtime, self.output, apply=True)
        self.assertEqual(result["journal_mode_after"], "delete")
        self.assertEqual(result["sql_rows_changed_by_repair"], 0)
        self.assertEqual(paper_status(self.path), before)
        self.assertEqual(storage.account_identity(self.path), identity)
        self.assertEqual((self.runtime / "cloud-manifest.json").read_bytes(), frozen)
        proof = json.loads(Path(result["proof"]).read_text())
        self.assertTrue(proof["backup"]["audit"]["passed"])
        self.assertTrue(proof["audit_after"]["passed"])
        self.assertEqual(proof["audit_after"]["baseline_books_reconciled"], 3)
        self.assertFalse(proof["read_only_container_access_verified"])
        self.assertTrue(Path(proof["backup"]["path"]).is_file())

    def test_preview_does_not_convert_or_create_evidence(self):
        result = storage.repair(self.runtime, self.output)
        self.assertFalse(result["apply"])
        self.assertEqual(storage.journal_mode(self.path), "wal")
        self.assertFalse(self.output.exists())

    def test_changed_freeze_or_closed_run_is_rejected(self):
        fingerprint = cloud.fingerprint()
        fingerprint["architecture"] = "different"
        with patch.object(cloud, "fingerprint", return_value=fingerprint):
            with self.assertRaises(ValueError):
                storage.repair(self.runtime, self.output, apply=True)
        (self.runtime / "COMPLETE").touch()
        with self.assertRaises(ValueError):
            storage.repair(self.runtime, self.output, apply=True)
        self.assertFalse(self.output.exists())
        self.assertEqual(storage.journal_mode(self.path), "wal")

    def test_uninitialized_account_and_public_output_are_rejected(self):
        with self.assertRaises(ValueError):
            storage.repair(self.root / "missing", self.output, apply=True)
        self.assertFalse((self.root / "missing").exists())
        with self.assertRaises(ValueError):
            storage.repair(self.runtime, cloud.ROOT / "reports/public-maintenance", apply=True)

    def test_new_ticks_and_reinitialization_keep_original_start_and_journal(self):
        original_start = forward_summary(self.path)["started_at"]
        storage.repair(self.runtime, self.output, apply=True)
        self.engine = PaperEngine(self.path, self.config)
        self.evaluation = ForwardEvaluation(self.path, self.config)
        self.tick(START + timedelta(seconds=5))
        summary = forward_summary(self.path)
        self.assertEqual(summary["started_at"], original_start)
        self.assertEqual(summary["observations"], 2)
        self.assertEqual(storage.journal_mode(self.path), "delete")
        self.assertTrue(audit_forward(self.path)["passed"])
        first = list(self.output.glob("*.json"))
        storage.repair(self.runtime, self.output, apply=True)
        self.assertTrue(all(path.exists() for path in first))

    def test_concurrent_ticks_survive_conversion(self):
        errors = []
        def collect():
            try:
                for i in range(1, 31):
                    self.tick(START + timedelta(seconds=5 * i))
                    time.sleep(0.01)
            except BaseException as exc:
                errors.append(exc)
        worker = threading.Thread(target=collect)
        worker.start()
        try:
            result = storage.repair(self.runtime, self.output, apply=True)
        finally:
            worker.join(timeout=15)
        self.assertFalse(worker.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(result["journal_mode_after"], "delete")
        self.assertEqual(forward_summary(self.path)["observations"], 31)
        self.assertTrue(audit_forward(self.path)["passed"])


if __name__ == "__main__":
    unittest.main()
