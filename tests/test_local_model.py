"""Pinned local inference, receipt validation and model-file integrity."""
import copy
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bitcoin_agent.local_adviser import LocalLlamaAdviser, verify_model_file
from bitcoin_agent.integration_io import DeliveryError
from tools import cloud_evaluation as cloud
from tools import prepare_open_model as download

ROOT = Path(__file__).resolve().parents[1]
ADVICE = {"suggestion":"prefer_cash","reason":"Unknown regime warrants caution.","limitation":"This does not predict future returns."}

class LocalModelTests(unittest.TestCase):
    def setUp(self):
        self.settings = json.loads((ROOT / "config/open-model.json").read_text(encoding="utf-8"))
        self.provider = LocalLlamaAdviser(self.settings)
        self.reply = {"id":"chatcmpl-unit-test","model":self.settings["alias"],
            "choices":[{"finish_reason":"stop","message":{"role":"assistant","content":json.dumps(ADVICE)}}],
            "usage":{"prompt_tokens":250,"completion_tokens":40,"total_tokens":290}}
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_schema_and_receipt_use_fixed_internal_endpoint_without_api_credentials(self):
        with patch("bitcoin_agent.local_adviser.http_json", return_value=self.reply) as request:
            self.assertEqual(self.provider.suggest({"features":{"regime":"unknown"}}), ADVICE)
        self.assertEqual(request.call_args.args[0], "http://model:8080/v1/chat/completions")
        body = request.call_args.kwargs["body"]
        self.assertEqual(body["max_tokens"], 256)
        self.assertTrue(body["response_format"]["json_schema"]["strict"])
        self.assertNotIn("tools", body)
        self.assertNotIn("headers", request.call_args.kwargs)
        self.assertEqual(self.provider.last_receipt["model_sha256"], self.settings["sha256"])

    def test_wrong_model_is_rejected(self):
        self.reply["model"] = "other-model"
        with patch("bitcoin_agent.local_adviser.http_json", return_value=self.reply), self.assertRaises(ValueError):
            self.provider.suggest({})

    def test_truncated_response_is_rejected(self):
        self.reply["choices"][0]["finish_reason"] = "length"
        with patch("bitcoin_agent.local_adviser.http_json", return_value=self.reply), self.assertRaises(ValueError):
            self.provider.suggest({})
        self.assertIsNone(self.provider.last_receipt)

    def test_tool_call_is_never_executed_or_accepted(self):
        self.reply["choices"][0]["message"]["tool_calls"] = [{"name":"trade"}]
        with patch("bitcoin_agent.local_adviser.http_json", return_value=self.reply), self.assertRaises(ValueError):
            self.provider.suggest({})

    def test_duplicate_advice_keys_are_rejected(self):
        self.reply["choices"][0]["message"]["content"] = '{"suggestion":"prefer_cash","suggestion":"prefer_mixed","reason":"x","limitation":"x"}'
        with patch("bitcoin_agent.local_adviser.http_json", return_value=self.reply), self.assertRaises(ValueError):
            self.provider.suggest({})

    def test_invented_allocation_fields_are_rejected(self):
        data = {**ADVICE, "allocation":0.95}
        self.reply["choices"][0]["message"]["content"] = json.dumps(data)
        with patch("bitcoin_agent.local_adviser.http_json", return_value=self.reply), self.assertRaises(ValueError):
            self.provider.suggest({})

    def test_missing_or_false_usage_cannot_become_genuine_receipt(self):
        for usage in ({}, {"prompt_tokens":True,"completion_tokens":40,"total_tokens":41},
                      {"prompt_tokens":250,"completion_tokens":40,"total_tokens":300},
                      {"prompt_tokens":250,"completion_tokens":257,"total_tokens":507}):
            self.reply["usage"] = usage
            with patch("bitcoin_agent.local_adviser.http_json", return_value=self.reply):
                self.assertEqual(self.provider.suggest({}), ADVICE)
                self.assertIsNone(self.provider.last_receipt)

    def test_oversized_context_is_not_sent(self):
        with patch("bitcoin_agent.local_adviser.http_json") as request, self.assertRaises(ValueError):
            self.provider.suggest({"data":"x"*4097})
        request.assert_not_called()

    def test_outage_has_no_paid_provider_fallback(self):
        with patch("bitcoin_agent.local_adviser.http_json", side_effect=DeliveryError("network_failure")) as request, self.assertRaises(DeliveryError):
            self.provider.suggest({})
        self.assertEqual(request.call_count, 1)
        self.assertIsNone(self.provider.last_receipt)

    def test_factory_ignores_inherited_openai_credential(self):
        with patch.dict("os.environ", {"OPENAI_API_KEY":"ambient-not-owner"}), \
             patch.object(cloud, "verify_model_file") as verified:
            provider = cloud.make_provider()
        self.assertIsInstance(provider, LocalLlamaAdviser)
        verified.assert_called_once()

    def test_model_file_hash_and_size_are_both_checked(self):
        target = self.root / "model.gguf"
        target.write_bytes(b"model-test")
        digest = hashlib.sha256(b"model-test").hexdigest()
        self.assertEqual(verify_model_file(target,digest,10),digest)
        with self.assertRaises(ValueError):
            verify_model_file(target,digest,9)
        with self.assertRaises(ValueError):
            verify_model_file(target,"0"*64,10)
        self.assertEqual(target.read_bytes(), b"model-test")

    def lock_for_download(self):
        lock = copy.deepcopy(self.settings)
        lock["sha256"] = hashlib.sha256(b"test-model").hexdigest()
        lock["bytes"] = len(b"test-model")
        (self.root/"config").mkdir()
        (self.root/"config/open-model.json").write_text(json.dumps(lock))
        return lock

    def test_download_is_publisher_pinned_and_verified_before_commit(self):
        lock = self.lock_for_download()
        target = self.root / "output/model.gguf"
        with patch.object(download,"ROOT",self.root), patch.object(download,"urlopen",return_value=io.BytesIO(b"test-model")) as request:
            result = download.prepare(target)
        self.assertEqual(result["sha256"],lock["sha256"])
        self.assertIn("/resolve/"+lock["revision"]+"/",request.call_args.args[0].full_url)
        self.assertEqual(target.read_bytes(), b"test-model")

    def test_wrong_download_never_becomes_active_model(self):
        self.lock_for_download()
        target = self.root / "output/model.gguf"
        with patch.object(download,"ROOT",self.root), patch.object(download,"urlopen",return_value=io.BytesIO(b"wrong-data")), self.assertRaises(ValueError):
            download.prepare(target)
        self.assertFalse(target.exists())

    def test_repeated_setup_keeps_existing_verified_model_without_network(self):
        self.lock_for_download()
        target=self.root/"model.gguf"
        target.write_bytes(b"test-model")
        with patch.object(download,"ROOT",self.root), patch.object(download,"urlopen") as request:
            self.assertEqual(download.prepare(target)["status"],"verified_existing")
        request.assert_not_called()

    def test_unapproved_publisher_is_rejected_before_network(self):
        lock = self.lock_for_download()
        lock["repository"] = "unapproved/model"
        (self.root/"config/open-model.json").write_text(json.dumps(lock))
        with patch.object(download,"ROOT",self.root), patch.object(download,"urlopen") as request, self.assertRaises(ValueError):
            download.prepare(self.root/"output/model.gguf")
        request.assert_not_called()

if __name__ == "__main__":
    unittest.main()
