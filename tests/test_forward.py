"""Prospective evidence, simultaneous books, restart ownership and durable backups."""

import copy
import json
import subprocess
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from bitcoin_agent.config import load_config, validate_config
from bitcoin_agent.forward import ForwardEvaluation, audit_forward, forward_summary, write_forward_report
from bitcoin_agent.paper_engine import PaperEngine
from bitcoin_agent.paper_store import connection, event, paper_status
from bitcoin_agent.remote_config import atomic_json
from bitcoin_agent.runtime import AwakeRequest, ProcessLock, backup_database, runtime_health, worker_python
from test_paper_engine import features, quote

ROOT = Path(__file__).resolve().parents[1]
START = datetime(2026, 10, 15, 12, tzinfo=timezone.utc)


class ForwardTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "var/test-temp"
        base.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "ledger.sqlite3"
        self.config = load_config(ROOT / "config/forward.json")
        self.engine = PaperEngine(self.path, self.config)
        self.evaluation = ForwardEvaluation(self.path, self.config, code_checksum="fixture-code")

    def observe(self, at=START, price="100", snapshot=True):
        q, f = quote(at, price), features(at) if snapshot else None
        result = self.engine.tick(q, f, at)
        self.evaluation.observe(q, f, result, at, self.config, self.engine.rules)
        return q, f, result

    def test_old_paper_history_cannot_become_a_new_forward_week(self):
        other = self.root / "old.sqlite3"
        engine = PaperEngine(other, self.config)
        engine.tick(quote(START), features(START), START)
        with self.assertRaisesRegex(ValueError, "fresh paper database"):
            ForwardEvaluation(other, self.config, code_checksum="fixture-code")

    def test_no_observations_has_no_started_clock_or_invented_results(self):
        result = forward_summary(self.path, START)
        self.assertEqual(result["observations"], 0)
        self.assertNotIn("started_at", result)
        self.assertFalse(result["seven_day_evidence_complete"])
        self.assertNotIn("strategies", result)

    def test_baseline_cash_conservation_and_shared_modeled_costs(self):
        self.observe()
        result = forward_summary(self.path, START + timedelta(seconds=5))
        self.assertEqual(set(result["strategies"]), {"candidate", "cash", "buy_and_hold", "scheduled_dca"})
        for book in ("scheduled_dca", "buy_and_hold"):
            row = result["strategies"][book]
            self.assertEqual(Decimal(row["equity_usd"]), Decimal(row["cash_usd"]) + Decimal(row["btc_units"]) * 100)
            self.assertLess(Decimal(row["equity_usd"]), Decimal("10000"))
        self.assertEqual(result["strategies"]["cash"]["equity_usd"], "10000.00")
        self.assertTrue(audit_forward(self.path)["passed"])

    def test_replayed_quote_and_restarted_evaluator_do_not_duplicate_baseline_fills(self):
        q, f, result = self.observe()
        before = forward_summary(self.path, START)
        restarted = ForwardEvaluation(self.path, self.config, code_checksum="fixture-code")
        restarted.observe(q, f, result, START, self.config, self.engine.rules)
        self.assertEqual(forward_summary(self.path, START), before)
        self.assertEqual(audit_forward(self.path)["baseline_books_reconciled"], 3)

    def test_source_config_and_dependency_change_reject_resume(self):
        with self.assertRaises(ValueError):
            ForwardEvaluation(self.path, self.config, code_checksum="changed")
        changed = self.config.data
        changed["dca"]["dip_trigger_fraction"] = .04
        with self.assertRaises(ValueError):
            ForwardEvaluation(self.path, validate_config(changed), code_checksum="fixture-code")
        with patch("bitcoin_agent.forward.environment_versions", return_value={"python": "different"}):
            with self.assertRaises(ValueError):
                ForwardEvaluation(self.path, self.config, code_checksum="fixture-code")

    def test_callback_failure_rolls_back_only_baseline_transaction(self):
        q, f = quote(START), features(START)
        result = self.engine.tick(q, f, START)
        with patch.object(self.evaluation, "_buy", side_effect=ArithmeticError("test failure")):
            with self.assertRaises(ArithmeticError):
                self.evaluation.observe(q, f, result, START, self.config, self.engine.rules)
        self.assertEqual(forward_summary(self.path, START)["observations"], 0)
        self.assertEqual(paper_status(self.path)["fills"], 1)
        self.assertTrue(audit_forward(self.path)["passed"])

    def test_uncommitted_or_different_quote_cannot_enter_evaluation(self):
        q, f = quote(START), features(START)
        result = {"accepted": True, "duplicate": False, "entries_enabled": True}
        with self.assertRaisesRegex(ValueError, "committed"):
            self.evaluation.observe(q, f, result, START, self.config, self.engine.rules)
        self.engine.tick(q, f, START)
        other = quote(START, "101", identity=q.quote_id)
        with self.assertRaisesRegex(ValueError, "committed"):
            self.evaluation.observe(other, f, result, START, self.config, self.engine.rules)

    def test_offline_day_is_a_gap_and_does_not_qualify(self):
        self.observe()
        self.observe(START + timedelta(days=1))
        result = forward_summary(self.path, START + timedelta(days=1))
        self.assertEqual(result["completed_days"], 1)
        self.assertEqual(result["qualifying_days"], 0)
        self.assertAlmostEqual(result["quote_coverage_fraction"], 120 / 86400)
        self.assertEqual(result["max_observation_gap_seconds"], 86400)
        self.assertFalse(result["seven_day_evidence_complete"])

    def test_seconds_between_same_received_trade_polls_count_once_each(self):
        self.observe()
        self.observe(START + timedelta(seconds=5))
        result = forward_summary(self.path, START + timedelta(seconds=10))
        self.assertEqual(result["observations"], 2)
        self.assertEqual(result["quote_coverage_fraction"], 1)
        self.assertEqual(result["completed_days"], 0)

    def test_stale_market_timestamp_shortens_coverage(self):
        q = quote(START - timedelta(seconds=100))
        # Exchange quote is 100 seconds old when actually received.
        result = self.engine.tick(q, features(START), START)
        self.evaluation.observe(q, features(START), result, START, self.config, self.engine.rules)
        report = forward_summary(self.path, START + timedelta(seconds=40))
        self.assertEqual(report["quote_coverage_fraction"], .5)

    def test_future_report_time_is_clipped_at_seven_days(self):
        self.observe()
        result = forward_summary(self.path, START + timedelta(days=8))
        self.assertEqual(result["elapsed_hours"], 168)
        self.assertEqual(result["completed_days"], 7)
        self.assertEqual(result["qualifying_days"], 0)
        self.assertFalse(result["seven_day_evidence_complete"])

    def test_seven_fully_covered_days_qualify_and_an_integrity_incident_disqualifies(self):
        # Constructed fixture for accounting logic only, never a real-data run.
        books = {name: {"equity_usd": "10000", "cash_usd": "10000", "btc_units": "0", "fees_usd": "0"}
                 for name in ("scheduled_dca", "buy_and_hold", "cash")}
        with connection(self.path, write=True) as database:
            for minute in range(7 * 1440 + 1):
                at = START + timedelta(minutes=minute)
                identity = "coverage-fixture-" + str(minute)
                database.execute("INSERT INTO paper_quotes VALUES (?,?,1,?)", (identity, at.isoformat(), "{}"))
                database.execute("INSERT INTO forward_observations VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                                 (identity, at.isoformat(), (at + timedelta(seconds=120)).isoformat(), 1, 0,
                                  self.config.checksum, "10000", "10000", "0", "active", json.dumps(books)))
        result = forward_summary(self.path, START + timedelta(days=7))
        self.assertEqual(result["completed_days"], 7)
        self.assertEqual(result["qualifying_days"], 7)
        self.assertTrue(result["seven_day_evidence_complete"])
        with connection(self.path, write=True) as database:
            event(database, START, "forward_observer_failure", {"reason": "fixture"})
        self.assertFalse(forward_summary(self.path, START + timedelta(days=7))["seven_day_evidence_complete"])

    def test_schedule_cap_and_restart_preserve_baseline_history(self):
        self.observe()
        self.observe(START + timedelta(seconds=5))
        self.observe(START + timedelta(hours=23))
        self.evaluation = ForwardEvaluation(self.path, self.config, code_checksum="fixture-code")
        self.observe(START + timedelta(hours=24))
        with connection(self.path) as database:
            fills = database.execute("SELECT COUNT(*) FROM forward_fills WHERE book='scheduled_dca'").fetchone()[0]
        self.assertEqual(fills, 2)
        self.assertTrue(audit_forward(self.path)["passed"])

    def test_baseline_tampering_is_rejected(self):
        self.observe()
        with connection(self.path, write=True) as database:
            database.execute("UPDATE forward_books SET state_json=? WHERE name='cash'", (json.dumps({"cash": "20000", "btc": "0", "fees": "0"}),))
        with self.assertRaises(ArithmeticError):
            audit_forward(self.path)

    def test_consistent_backup_reconciles_and_does_not_overwrite(self):
        self.observe()
        target = self.root / "backup.sqlite3"
        result = backup_database(self.path, target)
        self.assertTrue(result["audit"]["passed"])
        self.assertEqual(audit_forward(self.path), audit_forward(target))
        self.observe(START + timedelta(hours=24))
        self.assertNotEqual(paper_status(self.path)["fills"], paper_status(target)["fills"])
        with self.assertRaises(ValueError):
            backup_database(self.path, target)

    def test_process_lock_prevents_duplicate_writer_and_releases(self):
        path = self.root / "process.lock"
        with ProcessLock(path):
            with self.assertRaises(ValueError):
                with ProcessLock(path):
                    pass
        with ProcessLock(path):
            self.assertTrue(path.exists())

    def test_worker_process_handle_owns_actual_python_and_loads_same_libraries(self):
        executable, environment = worker_python()
        code = "import os,json,numpy,pandas; print(json.dumps({'pid':os.getpid(),'numpy':numpy.__version__,'pandas':pandas.__version__}))"
        child = subprocess.Popen([executable, "-c", code], env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output, errors = child.communicate(timeout=15)
        self.assertEqual(child.returncode, 0, errors)
        observed = json.loads(output)
        self.assertEqual(observed["pid"], child.pid)
        self.assertEqual(observed["numpy"], __import__("numpy").__version__)
        self.assertEqual(observed["pandas"], __import__("pandas").__version__)

    def test_read_only_health_reports_missing_stale_and_future_heartbeats(self):
        self.assertFalse(runtime_health(self.root, START)["healthy"])
        supervisor = {"state": "running", "heartbeat_at": START.isoformat()}
        worker = {"state": "running", "heartbeat_at": START.isoformat(), "last_accepted_quote_at": START.isoformat(), "features_fresh": True}
        atomic_json(self.root / "supervisor.json", supervisor)
        atomic_json(self.root / "worker.json", worker)
        self.assertTrue(runtime_health(self.root, START)["healthy"])
        self.assertFalse(runtime_health(self.root, START + timedelta(minutes=5))["healthy"])
        self.assertFalse(runtime_health(self.root, START - timedelta(minutes=5))["healthy"])

    def test_awake_request_restores_prior_flags_when_supervisor_fails(self):
        with patch("bitcoin_agent.runtime._execution_state", return_value=0x80000002) as request:
            with self.assertRaisesRegex(RuntimeError, "fixture crash"):
                with AwakeRequest(enabled=True):
                    raise RuntimeError("fixture crash")
        self.assertEqual([call.args[0] for call in request.call_args_list], [0x80000001, 0x80000002])

    def test_awake_request_failure_prevents_an_unprotected_start(self):
        with patch("bitcoin_agent.runtime._execution_state", return_value=0) as request:
            with self.assertRaisesRegex(OSError, "awake request"):
                with AwakeRequest(enabled=True):
                    self.fail("A rejected Windows request must not enter the supervisor")
        request.assert_called_once_with(0x80000001)

    def test_disabled_awake_request_never_calls_windows(self):
        with patch("bitcoin_agent.runtime._execution_state") as request:
            with AwakeRequest(enabled=False):
                pass
        request.assert_not_called()

    def test_report_can_be_viewed_without_network_and_shows_unfinished_week(self):
        self.observe()
        summary = write_forward_report(self.path, self.root / "report", START + timedelta(hours=1))
        self.assertFalse(summary["seven_day_evidence_complete"])
        self.assertIn("**0/7**", (self.root / "report/evaluation.md").read_text(encoding="utf-8"))

    def test_freeze_violation_or_observer_incident_prevents_clean_evidence(self):
        self.observe()
        with connection(self.path, write=True) as database:
            event(database, START, "forward_observer_failure", {"reason": "test"})
        result = forward_summary(self.path, START + timedelta(days=7))
        self.assertEqual(result["observer_failures"], 1)
        self.assertFalse(result["seven_day_evidence_complete"])


if __name__ == "__main__":
    unittest.main()
