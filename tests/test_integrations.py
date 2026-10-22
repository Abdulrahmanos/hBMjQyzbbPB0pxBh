"""Integration failures must preserve risk, capital and delivery/report identity."""

import copy
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch
from threading import Event
from types import SimpleNamespace

from bitcoin_agent.adviser import FixtureAdviser, OpenAIAdviser, ShadowAdviser, validate_advice
from bitcoin_agent.config import ConfigError, load_config, validate_config
from bitcoin_agent.integration_io import DeliveryError, load_env
from bitcoin_agent.integrations import IntegrationCoordinator, load_integration_settings
from bitcoin_agent.notifications import (DemoInbox, GmailSender, TelegramSender, dispatch, initialize_outbox,
    outbox_status, queue_fills, queue_weekly, render_inbox, report_boundary, retry_message, weekly_report)
from bitcoin_agent.paper_engine import PaperEngine
from bitcoin_agent.paper_store import audit_paper, connection, paper_status
from bitcoin_agent.remote_config import ConfigManager, LocalRows, candidate_from_patch, control_rows, parse_rows
from test_paper_engine import features, quote

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 5, 6, tzinfo=timezone.utc)  # Monday 09:00 Riyadh


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / "var/test-temp"
        base.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=base)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.path = self.root / "paper.sqlite3"
        self.config = load_config(ROOT / "config/demo.json")
        self.engine = PaperEngine(self.path, self.config)
        self.rows = control_rows(self.config)
        self.source = self.root / "rows.json"
        self.source.write_text(json.dumps(self.rows), encoding="utf-8")
        self.cache = self.root / "cache.json"
        initialize_outbox(self.path)

    def fill(self, now=NOW):
        return self.engine.tick(quote(now), features(now), now)

    def test_sheet_types_duplicates_missing_unknown_and_nonfinite_rejected(self):
        self.assertEqual(candidate_from_patch(self.config, parse_rows(self.rows)).checksum, self.config.checksum)
        for bad in (self.rows + [self.rows[1]], self.rows[:-1], self.rows + [["portfolio.initial_cash_usd", "20000"]],
                    self.rows + [["api_secret", "x"]]):
            with self.assertRaises(ConfigError):
                parse_rows(bad)
        for invalid in (True, float("inf"), "NaN", "=IMPORTDATA(\"x\")", "0.03 "):
            bad = copy.deepcopy(self.rows)
            bad[4][1] = invalid
            if invalid == "0.03 ":
                self.assertEqual(parse_rows(bad)["dca.dip_trigger_fraction"], .03)
            else:
                with self.assertRaises((ConfigError, ValueError)):
                    candidate_from_patch(self.config, parse_rows(bad))

    def test_remote_cannot_raise_local_spending_risk_or_bear_allocation(self):
        original = parse_rows(self.rows)
        for key, value in (("dca.tranche_fraction_of_dca_budget", .03), ("dca.max_spend_fraction_per_7_days", .15),
                           ("swing.risk_fraction_of_equity", .006), ("operational.bear_targets", [.1, 0, .9])):
            with self.subTest(key=key), self.assertRaises(ConfigError):
                candidate_from_patch(self.config, {**original, key: value})
        self.assertEqual(self.config.data["operational"]["max_lifetime_loss_fraction"], .3)

    def test_native_sheet_integral_float_keeps_semantic_config_checksum(self):
        changes = parse_rows(self.rows)
        changes["swing.take_profit_r_multiple"] = 2
        self.assertEqual(candidate_from_patch(self.config, changes).checksum, self.config.checksum)

    def test_last_valid_cache_survives_bad_remote_and_restart(self):
        manager = ConfigManager(self.config, self.cache, LocalRows(self.source))
        updated = copy.deepcopy(self.rows)
        updated[4][1] = .04
        self.source.write_text(json.dumps(updated), encoding="utf-8")
        accepted = manager.refresh(NOW)
        self.assertTrue(accepted["changed"])
        preserved = self.cache.read_bytes()
        self.source.write_text("bad-json", encoding="utf-8")
        self.assertFalse(manager.refresh(NOW + timedelta(hours=1))["changed"])
        self.assertEqual(self.cache.read_bytes(), preserved)
        restarted = ConfigManager(self.config, self.cache, LocalRows(self.source))
        self.assertEqual(restarted.current.data["dca"]["dip_trigger_fraction"], .04)
        self.assertFalse(restarted.due(NOW + timedelta(seconds=3599)))
        self.assertTrue(restarted.due(NOW + timedelta(hours=1)))

    def test_broken_or_wrong_baseline_cache_uses_local_baseline(self):
        self.cache.write_text("broken", encoding="utf-8")
        manager = ConfigManager(self.config, self.cache)
        self.assertEqual(manager.current.checksum, self.config.checksum)
        self.assertIn("invalid_cache", manager.status)
        ConfigManager(self.config, self.cache, LocalRows(self.source)).refresh(NOW)
        changed = self.config.data
        changed["risk"]["max_portfolio_drawdown_fraction"] = .2
        other = ConfigManager(validate_config(changed), self.cache)
        self.assertIn("invalid_cache", other.status)

    def test_atomic_cache_write_failure_does_not_accept_config(self):
        manager = ConfigManager(self.config, self.cache, LocalRows(self.source))
        self.rows[4][1] = .04
        self.source.write_text(json.dumps(self.rows), encoding="utf-8")
        with patch("bitcoin_agent.remote_config.os.replace", side_effect=OSError("disk failed")):
            self.assertFalse(manager.refresh(NOW)["changed"])
        self.assertEqual(manager.current.checksum, self.config.checksum)

    def test_hot_edit_releases_buy_reservation_but_preserves_cash_inventory_and_risk_state(self):
        data = self.config.data
        data["operational"]["paper_fill_cap_usd"] = "20"
        baseline = validate_config(data)
        engine = PaperEngine(self.path, baseline)
        engine.tick(quote(NOW), features(NOW), NOW)
        before = paper_status(self.path)
        self.assertTrue(before["active_orders"])
        changes = parse_rows(control_rows(baseline))
        changes["dca.enabled"] = False
        engine.apply_config(candidate_from_patch(baseline, changes), NOW + timedelta(seconds=1))
        after = paper_status(self.path)
        self.assertEqual(after["active_orders"], [])
        for key in ("cash_usd", "btc_units", "risk_epoch", "max_lifetime_drawdown"):
            self.assertEqual(before[key], after[key])
        self.assertTrue(audit_paper(self.path)["passed"])
        self.assertEqual(engine.config.initial_cash, Decimal("10000"))

    def test_edit_does_not_widen_existing_swing_stop(self):
        data = self.config.data
        data["dca"]["enabled"] = False
        data["swing"]["enabled"] = True
        baseline = validate_config(data)
        engine = PaperEngine(self.path, baseline)
        engine.tick(quote(NOW), features(NOW, swing=True), NOW)
        with connection(self.path) as database:
            stop = database.execute("SELECT stop_price FROM paper_positions WHERE sleeve='swing'").fetchone()[0]
        self.assertIsNotNone(stop)
        changes = parse_rows(control_rows(baseline))
        changes["swing.atr_multiplier"] = 3
        engine.apply_config(candidate_from_patch(baseline, changes), NOW + timedelta(seconds=1))
        with connection(self.path) as database:
            self.assertEqual(database.execute("SELECT stop_price FROM paper_positions WHERE sleeve='swing'").fetchone()[0], stop)

    def test_fill_delivery_scan_restart_idempotency_and_no_ledger_change(self):
        self.fill()
        before = audit_paper(self.path)
        queue_fills(self.path, NOW)
        sender = DemoInbox(self.root / "inbox")
        self.assertEqual(len(dispatch(self.path, {"telegram": sender}, NOW)), 1)
        queue_fills(self.path, NOW + timedelta(seconds=5))
        self.assertEqual(dispatch(self.path, {"telegram": sender}, NOW + timedelta(seconds=5)), [])
        self.assertEqual(len(list((self.root / "inbox").glob("*.json"))), 1)
        self.assertEqual(audit_paper(self.path), before)

    def test_known_transient_failure_waits_then_retries(self):
        self.fill()
        queue_fills(self.path, NOW)
        class Broken:
            local = False
            def send(self, *args):
                raise DeliveryError("http_429", retry_after=120)
        self.assertEqual(dispatch(self.path, {"telegram": Broken()}, NOW)[0]["status"], "pending")
        sender = DemoInbox(self.root / "inbox")
        self.assertEqual(dispatch(self.path, {"telegram": sender}, NOW + timedelta(seconds=60)), [])
        self.assertEqual(dispatch(self.path, {"telegram": sender}, NOW + timedelta(seconds=120))[0]["status"], "delivered")
        self.assertEqual(outbox_status(self.path)[0]["attempts"], 2)

    def test_ambiguous_live_timeout_stops_until_explicit_retry(self):
        self.fill()
        queue_fills(self.path, NOW)
        class Broken:
            local = False
            def send(self, *args):
                raise DeliveryError("network_failure", ambiguous=True)
        first = dispatch(self.path, {"telegram": Broken()}, NOW)[0]
        self.assertEqual(first["status"], "uncertain")
        sender = DemoInbox(self.root / "inbox")
        self.assertEqual(dispatch(self.path, {"telegram": sender}, NOW + timedelta(days=1)), [])
        retry_message(self.path, first["id"], NOW + timedelta(days=1))
        self.assertEqual(dispatch(self.path, {"telegram": sender}, NOW + timedelta(days=1))[0]["status"], "delivered")
        with self.assertRaises(ValueError):
            retry_message(self.path, first["id"], NOW + timedelta(days=2))

    def test_abandoned_live_claim_uncertain_local_claim_recoverable(self):
        self.fill()
        queue_fills(self.path, NOW)
        with connection(self.path, write=True) as database:
            database.execute("UPDATE notification_outbox SET status='sending',claimed_at=?", (NOW.isoformat(),))
        class Live:
            local = False
        self.assertEqual(dispatch(self.path, {"telegram": Live()}, NOW + timedelta(minutes=3)), [])
        self.assertEqual(outbox_status(self.path)[0]["status"], "uncertain")
        with connection(self.path, write=True) as database:
            database.execute("UPDATE notification_outbox SET status='sending',claimed_at=?", (NOW.isoformat(),))
        self.assertEqual(dispatch(self.path, {"telegram": DemoInbox(self.root / "inbox")}, NOW + timedelta(minutes=3))[0]["status"], "delivered")

    def test_weekly_schedule_before_at_after_monday_and_restart(self):
        reporting = self.config.data["reporting"]
        self.assertEqual(report_boundary(NOW, reporting), NOW)
        self.assertEqual(report_boundary(NOW - timedelta(seconds=1), reporting), NOW - timedelta(days=7))
        self.assertEqual(report_boundary(NOW + timedelta(days=2), reporting), NOW)
        self.fill(NOW - timedelta(days=1))
        first = queue_weekly(self.path, self.config, NOW)
        self.assertEqual(queue_weekly(self.path, self.config, NOW + timedelta(days=2)), first)
        self.assertEqual(len(outbox_status(self.path)), 1)

    def test_report_no_history_does_not_fabricate_zero_return(self):
        report = weekly_report(self.path, NOW, NOW)
        self.assertIsNone(report["metrics"]["net_return_fraction"])
        self.assertIsNone(report["metrics"]["closing_equity_usd"])
        self.assertIsNone(queue_weekly(self.path, self.config, NOW))

    def test_partial_week_and_stale_closing_prices_labelled(self):
        self.fill(NOW - timedelta(days=1))
        report = weekly_report(self.path, NOW, NOW)
        self.assertIn("Partial period", report["metrics"]["coverage"])
        self.assertTrue(report["metrics"]["closing_mark_stale"])
        self.assertEqual(report["metrics"]["fills"], 1)
        self.assertEqual(Decimal(report["metrics"]["fees_usd"]), Decimal("0.60"))

    def test_report_filters_future_fills_and_independent_return_drawdown(self):
        self.fill(NOW - timedelta(days=8))
        self.engine.tick(quote(NOW - timedelta(days=7), "110"), features(NOW - timedelta(days=7)), NOW - timedelta(days=7))
        self.engine.tick(quote(NOW, "108"), features(NOW), NOW)
        report = weekly_report(self.path, NOW, NOW)
        opening = Decimal(report["metrics"]["opening_equity_usd"])
        closing = Decimal(report["metrics"]["closing_equity_usd"])
        self.assertEqual(Decimal(report["metrics"]["net_return_fraction"]), closing / opening - 1)
        self.assertEqual(Decimal(report["metrics"]["observed_max_drawdown_fraction"]), 1 - closing / opening)
        before = json.dumps(report["metrics"], sort_keys=True)
        self.engine.tick(quote(NOW + timedelta(days=1), "109"), features(NOW + timedelta(days=1)), NOW + timedelta(days=1))
        self.assertEqual(json.dumps(weekly_report(self.path, NOW, NOW)["metrics"], sort_keys=True), before)

    def test_advice_budget_restart_and_no_execution_effect(self):
        result = self.fill()
        adviser = ShadowAdviser(self.path, FixtureAdviser())
        before = audit_paper(self.path)
        first = adviser.observe(self.config, features(NOW), result["portfolio"], NOW)
        self.assertEqual(first["status"], "shadow_recorded")
        self.assertFalse(first["applied_to_trades"])
        restarted = ShadowAdviser(self.path, FixtureAdviser())
        self.assertEqual(restarted.observe(self.config, features(NOW), result["portfolio"], NOW)["status"], "already_observed")
        next_time = NOW + timedelta(hours=1)
        self.assertEqual(restarted.observe(self.config, features(next_time), result["portfolio"], next_time)["status"], "rate_budget_reached")
        self.assertEqual(audit_paper(self.path), before)

    def test_bad_advice_and_outage_fall_back_to_rules(self):
        for bad in ({"suggestion": "buy", "reason": "x", "limitation": "x"},
                    {"suggestion": "prefer_cash", "reason": "x", "limitation": "x", "risk": .99},
                    {"suggestion": "prefer_cash", "reason": "", "limitation": "x"}):
            with self.assertRaises(ValueError):
                validate_advice(bad)
        class Broken:
            model = "test-only"
            def suggest(self, context):
                raise DeliveryError("network_failure")
        result = self.fill()
        adviser = ShadowAdviser(self.path, Broken())
        self.assertEqual(adviser.observe(self.config, features(NOW), result["portfolio"], NOW)["status"], "rejected_or_unavailable")
        self.assertEqual(adviser.observe(self.config, features(NOW), result["portfolio"], NOW)["status"], "already_observed")
        self.assertTrue(audit_paper(self.path)["passed"])

    def test_stale_features_never_sent_to_adviser(self):
        result = self.fill()
        provider = FixtureAdviser()
        with patch.object(provider, "suggest", side_effect=AssertionError("must not call")):
            adviser = ShadowAdviser(self.path, provider)
            self.assertEqual(adviser.observe(self.config, features(NOW), result["portfolio"], NOW + timedelta(hours=3))["status"], "skipped_stale_features")

    def test_daily_attempt_quota_survives_failures_and_restart(self):
        result = self.fill()
        class Broken:
            model = "quota-test"
            def suggest(self, context):
                raise DeliveryError("network_failure")
        for hours in (0, 1, 2, 3):
            now = NOW + timedelta(hours=hours)
            adviser = ShadowAdviser(self.path, Broken(), daily_calls=4, interval_hours=1)
            self.assertEqual(adviser.observe(self.config, features(now), result["portfolio"], now)["status"], "rejected_or_unavailable")
        now = NOW + timedelta(hours=4)
        self.assertEqual(adviser.observe(self.config, features(now), result["portfolio"], now)["status"], "rate_budget_reached")

    def test_background_integration_latency_does_not_stop_quote_ticks(self):
        from bitcoin_agent.paper_service import PRODUCT_URL, run_paper
        entered, release = Event(), Event()
        tick_count = [0]
        config = self.config
        class SlowCoordinator:
            manager = SimpleNamespace(current=config)
            def __init__(self, *args):
                pass
            def poll(self, *args):
                entered.set()
                if not release.wait(3):
                    raise ValueError("Test stalled")
                return {"config": config, "config_refresh": None, "delivery": [], "advice": None}
            def preview(self):
                return {"delivery_mode": "test"}
        def public(url):
            if url == PRODUCT_URL:
                return {"id": "BTC-USD", "base_increment": "0.00000001", "quote_increment": "0.01", "min_market_funds": "1", "status": "online"}
            tick_count[0] += 1
            if tick_count[0] == 2:
                self.assertTrue(entered.wait(2))
                self.assertFalse(release.is_set())
            if tick_count[0] == 3:
                release.set()
            now = datetime.now(timezone.utc)
            return {"quote_id": "async-" + str(tick_count[0]), "time": now.isoformat(), "bid": "99.99", "ask": "100.01", "price": "100"}
        with patch("bitcoin_agent.integrations.IntegrationCoordinator", SlowCoordinator), patch("bitcoin_agent.paper_service.public_json", side_effect=public), patch("bitcoin_agent.paper_service.refresh_features", return_value=features(datetime.now(timezone.utc))), patch("bitcoin_agent.paper_service.time.sleep"):
            result = run_paper(self.path, config, cycles=3, runtime=self.root / "live", progress=lambda _: None, integration_settings={})
        self.assertEqual(result["accepted_quotes"], 3)
        self.assertFalse(result["running"])
        self.assertTrue(result["audit"]["passed"])

    def test_live_sender_requests_are_paper_only_and_oauth_separate(self):
        with patch.dict(os.environ, {"TELEGRAM_BOT_TOKEN": "123:demo_only", "TELEGRAM_CHAT_ID": "-123"}), patch("bitcoin_agent.notifications.http_json", return_value={"ok": True, "result": {"message_id": 4}}) as request:
            self.assertEqual(TelegramSender().send("id", "telegram", {"text": "PAPER test"}), "4")
            self.assertIn("sendMessage", request.call_args.args[0])
        with patch.dict(os.environ, {"GMAIL_REPORT_RECIPIENT": "demo@example.invalid"}), patch("bitcoin_agent.notifications.oauth_token", return_value="test-only"), patch("bitcoin_agent.notifications.http_json", return_value={"id": "mail"}) as request:
            self.assertEqual(GmailSender().send("id", "gmail", {"subject": "PAPER", "text": "test"}), "mail")
            self.assertEqual(request.call_args.args[0], "https://gmail.googleapis.com/gmail/v1/users/me/messages/send")
        with patch.dict(os.environ, {"GMAIL_REPORT_RECIPIENT": "bad\naddress"}):
            with self.assertRaises(DeliveryError):
                GmailSender().send("id", "gmail", {})

    def test_openai_request_shape_and_refusal(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "test-only", "LLM_MODEL": "explicit-test-model"}), patch("bitcoin_agent.adviser.http_json", return_value={"status": "completed", "output": [{"type": "message", "content": [{"type": "refusal"}]}]}) as request:
            with self.assertRaises(ValueError):
                OpenAIAdviser().suggest({"features": {}})
            body = request.call_args.kwargs["body"]
            self.assertFalse(body["store"])
            self.assertEqual(body["max_output_tokens"], 300)
            self.assertTrue(body["text"]["format"]["strict"])
            self.assertNotIn("tools", body)

    def test_env_literals_no_interpolation_and_existing_environment_preserved(self):
        target = self.root / ".env"
        target.write_text('OPENAI_API_KEY="literal-$NOT_A_VARIABLE"\nLLM_MODEL=test-model\n', encoding="utf-8")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "existing"}):
            load_env(target)
            self.assertEqual(os.environ["OPENAI_API_KEY"], "existing")
            self.assertEqual(os.environ["LLM_MODEL"], "test-model")
        target.write_text("PATH=unexpected", encoding="utf-8")
        with self.assertRaises(ValueError):
            load_env(target)

    def test_coordinator_hourly_rejection_backoff_and_escaped_preview(self):
        self.fill()
        settings = load_integration_settings(ROOT / "config/integrations.demo.json")
        settings.update(local_rows_path=str(self.source), cache_path=str(self.cache), inbox_path=str(self.root / "inbox"))
        coordinator = IntegrationCoordinator(self.path, self.config, settings)
        self.source.write_text("bad-json", encoding="utf-8")
        self.assertIsNotNone(coordinator.poll(now=NOW)["config_refresh"])
        self.assertIsNone(coordinator.poll(now=NOW + timedelta(seconds=5))["config_refresh"])
        with connection(self.path, write=True) as database:
            payload = {"subject": "<script>bad</script>", "text": "<img src=x onerror=alert(1)>"}
            database.execute("UPDATE notification_outbox SET payload_json=?", (json.dumps(payload),))
        text = render_inbox(self.path, self.root / "inbox").read_text(encoding="utf-8")
        self.assertNotIn("<script>", text)
        self.assertIn("&lt;script&gt;", text)


if __name__ == "__main__":
    unittest.main()
