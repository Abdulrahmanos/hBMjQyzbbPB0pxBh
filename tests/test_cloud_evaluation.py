"""Cloud isolation, frozen protocol, genuine receipts and no backfilled uptime."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from bitcoin_agent.adviser import OpenAIAdviser, ShadowAdviser
from bitcoin_agent.local_adviser import LocalLlamaAdviser
from bitcoin_agent.config import load_config
from bitcoin_agent.forward import ForwardEvaluation
from bitcoin_agent.paper_engine import PaperEngine
from bitcoin_agent.paper_store import audit_paper, connection, paper_status
from bitcoin_agent.remote_config import atomic_json
from tools import cloud_evaluation as cloud
from test_paper_engine import features, quote

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
ADVICE = {"suggestion":"prefer_cash","reason":"Observed bearish context.","limitation":"No future prices are known."}


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.runtime = self.root / "paper"
        self.state = self.root / "adviser"
        self.state.mkdir()
        self.runtime.mkdir()
        self.config = load_config(cloud.CONFIG)

    def response(self):
        return {"id":"chatcmpl-unit_test","model":"bitcoin-qwen-0.5b",
                "usage":{"prompt_tokens":240,"completion_tokens":60,"total_tokens":300},
                "choices":[{"finish_reason":"stop","message":{"role":"assistant","content":json.dumps(ADVICE)}}]}

    def account(self):
        cloud.freeze(self.runtime)
        path = self.runtime / "ledger.sqlite3"
        engine = PaperEngine(path, self.config)
        evaluation = ForwardEvaluation(path, self.config)
        q, f = quote(NOW), features(NOW)
        result = engine.tick(q, f, NOW)
        evaluation.observe(q, f, result, NOW, self.config, engine.rules)
        atomic_json(self.runtime / "market/features.json", f.to_dict())
        return path

    def provider(self):
        return ShadowAdviser(self.state / "shadow.sqlite3", LocalLlamaAdviser(json.loads((ROOT / "config/open-model.json").read_text(encoding="utf-8"))))

    def test_private_file_does_not_use_inherited_credentials(self):
        path = self.root / ".env"
        path.write_text("OPENAI_API_KEY=\n")
        with patch.dict(os.environ, {"OPENAI_API_KEY":"inherited-test"}):
            with self.assertRaises(ValueError):
                cloud.private_key(path)
            path.write_text('OPENAI_API_KEY="owner-test"\n')
            self.assertEqual(cloud.private_key(path), "owner-test")
            path.write_text("OPENAI_API_KEY=one\nOPENAI_API_KEY=two\n")
            with self.assertRaises(ValueError):
                cloud.private_key(path)
        with self.assertRaises(ValueError):
            cloud.prepare_secret(path, self.root / "published.env")

    def test_freeze_restart_keeps_identity_and_rejects_changed_environment(self):
        first = cloud.freeze(self.runtime)
        self.assertEqual(first, cloud.freeze(self.runtime))
        changed = cloud.fingerprint()
        changed["environment"] = {"changed":True}
        with patch.object(cloud, "fingerprint", return_value=changed):
            with self.assertRaises(ValueError):
                cloud.freeze(self.runtime)
        self.assertEqual(json.loads((self.runtime / "cloud-manifest.json").read_text(encoding="utf-8")), first)

    def test_existing_or_corrupt_account_cannot_become_new_experiment(self):
        (self.runtime / "ledger.sqlite3").touch()
        with self.assertRaises(ValueError):
            cloud.freeze(self.runtime)
        (self.runtime / "cloud-manifest.json").write_text("broken")
        with self.assertRaises(ValueError):
            cloud.freeze(self.runtime)

    def test_observer_cannot_rebind_existing_state_to_other_run(self):
        cloud.freeze(self.runtime)
        cloud.bind_observer(self.runtime, self.state)
        saved = json.loads((self.runtime / "cloud-manifest.json").read_text(encoding="utf-8"))
        saved["run_id"] = "different"
        atomic_json(self.runtime / "cloud-manifest.json", saved)
        with self.assertRaises(ValueError):
            cloud.bind_observer(self.runtime, self.state)

    def test_real_response_receipt_is_recorded_without_mutating_paper(self):
        path = self.account()
        before = audit_paper(path)
        provider = self.provider()
        with patch.dict(os.environ, {"OPENAI_API_KEY":"test-only","LLM_MODEL":"gpt-6-luna"}), \
             patch.object(cloud, "runtime_health", return_value={"healthy":True}), \
             patch("bitcoin_agent.local_adviser.http_json", return_value=self.response()) as request:
            result = cloud.observe_once(self.runtime, self.state, self.config, provider, NOW)
            self.assertTrue(result["provider_receipt_verified"])
            self.assertFalse(result["applied_to_trades"])
            self.assertTrue(request.call_args.kwargs["body"]["response_format"]["json_schema"]["strict"])
            self.assertNotIn("Authorization", request.call_args.kwargs.get("headers", {}))
            self.assertEqual(cloud.observe_once(self.runtime, self.state, self.config, provider, NOW)["status"], "already_observed")
            self.assertEqual(request.call_count, 1)
        receipts = list((self.state / "receipts").glob("*.json"))
        self.assertEqual(len(receipts), 1)
        receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(receipt["response_id"], "chatcmpl-unit_test")
        self.assertNotIn("test-only", receipts[0].read_text(encoding="utf-8"))
        self.assertEqual(audit_paper(path), before)
        with connection(path) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='adviser_records'").fetchone())

    def test_stopped_or_unhealthy_paper_never_calls_provider(self):
        self.account()
        provider = self.provider()
        with patch.object(provider.provider, "suggest") as request:
            with patch.object(cloud, "runtime_health", return_value={"healthy":False}):
                self.assertEqual(cloud.observe_once(self.runtime, self.state, self.config, provider, NOW)["status"], "waiting_for_fresh_paper_data")
            (self.runtime / "COMPLETE").touch()
            self.assertEqual(cloud.observe_once(self.runtime, self.state, self.config, provider, NOW)["status"], "stopped")
            request.assert_not_called()

    def test_lifetime_attempt_cap_survives_restart(self):
        self.account()
        provider = self.provider()
        with connection(provider.path, write=True) as db:
            for number in range(28):
                db.execute("INSERT INTO adviser_records VALUES (?,?,?,?,?,?,?,?)",
                           (str(number), NOW.isoformat(), "gpt-6-luna", "test", "{}", "rejected_or_unavailable", None, "test"))
        with patch.object(cloud, "runtime_health", return_value={"healthy":True}), \
             patch.object(provider.provider, "suggest") as request:
            self.assertEqual(cloud.observe_once(self.runtime, self.state, self.config, self.provider(), NOW)["status"], "total_attempt_budget_reached")
            request.assert_not_called()

    def test_valid_json_without_provider_metadata_is_not_claimed_as_proof(self):
        response = self.response()
        del response["id"]
        provider = self.provider().provider
        with patch.dict(os.environ, {"OPENAI_API_KEY":"test-only","LLM_MODEL":"gpt-6-luna"}), \
             patch("bitcoin_agent.local_adviser.http_json", return_value=response):
            self.assertEqual(provider.suggest({"features":{}}), ADVICE)
            self.assertIsNone(provider.last_receipt)

    def test_interim_export_cannot_pass_week_or_llm_checks(self):
        self.account()
        report = cloud.export_review(self.runtime, self.state, self.root / "report")
        self.assertFalse(report["complete"])
        self.assertFalse(report["checks"]["seven_prospective_days_pass"])
        self.assertFalse(report["checks"]["real_model_response_recorded"])
        self.assertTrue(report["checks"]["candidate_and_three_baselines_reconcile"])
        self.assertTrue(report["checks"]["all_accepted_quotes_paired"])
        with self.assertRaises(ValueError):
            cloud.export_review(self.runtime, self.state, self.root / "final", require_complete=True)

    def test_repeated_genuine_preflight_preserves_receipt_and_does_not_regenerate(self):
        path = self.account()
        before = audit_paper(path)
        provider = self.provider().provider
        output = self.root / "var/probe"
        frozen = cloud.fingerprint()
        with patch.object(cloud, "fingerprint", return_value=frozen), patch.object(cloud, "ROOT", self.root), patch.object(cloud, "now_utc", return_value=NOW), \
             patch.object(cloud, "make_provider", return_value=provider), \
             patch("bitcoin_agent.local_adviser.http_json", return_value=self.response()) as request:
            first = cloud.probe_llm(None, output, self.runtime)
            saved = (output / "verification.json").read_bytes()
            second = cloud.probe_llm(None, output, self.runtime)
        self.assertTrue(first["provider_response_verified"])
        self.assertEqual(second["status"], "already_verified")
        self.assertEqual(request.call_count, 1)
        self.assertEqual((output / "verification.json").read_bytes(), saved)
        self.assertEqual(len(list((output / "attempts").glob("*.json"))), 1)
        self.assertEqual(audit_paper(path), before)

    def test_compose_adviser_has_read_only_paper_mount_and_no_external_secrets(self):
        compose = json.loads((ROOT / "compose.evaluation.yaml").read_text(encoding="utf-8"))
        self.assertIn("cloud_paper:/app/evidence:ro", compose["services"]["adviser"]["volumes"])
        self.assertNotIn("secrets", compose["services"]["paper"])
        self.assertNotIn("secrets", compose)
        self.assertTrue(compose["networks"]["inference"]["internal"])
        self.assertNotIn("ports", compose["services"]["model"])
        self.assertIn("model_weights:/models:ro", compose["services"]["adviser"]["volumes"])
        self.assertNotIn("ports", compose["services"]["paper"])


if __name__ == "__main__":
    unittest.main()
