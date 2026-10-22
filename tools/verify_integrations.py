"""Independent reconciliation of milestone-4 saved demo artifacts and report marks."""

import hashlib
import json
import platform
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from bitcoin_agent.config import load_config
from bitcoin_agent.paper_store import audit_paper, connection, paper_status
from bitcoin_agent.remote_config import candidate_from_patch, parse_rows
from bitcoin_agent.storage import read_status

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/milestone-4"


def read(name):
    return json.loads((OUT / name).read_text(encoding="utf-8"))


def verify():
    config = load_config(ROOT / "config/demo.json")
    native = read("sheet-readback.json")
    assert candidate_from_patch(config, parse_rows(native["values"])).checksum == config.checksum
    assert native["values"] == json.loads((ROOT / "config/sheet-rows.json").read_text(encoding="utf-8"))
    assert native["mime_type"] == "application/vnd.google-apps.spreadsheet" and native["private"]
    raw_hash = hashlib.sha256((ROOT / "data/raw/btc_usd_1h.csv").read_bytes()).hexdigest()
    assert raw_hash == "31ae7877085693949570f3d343060c3340ffe8b292200001274b0816762c8080"
    foundation = read_status(ROOT / "var/paper.sqlite3")
    assert foundation["event_count"] == 1
    assert sum(Decimal(row["cash_usd"]) for row in foundation["balances"]) == Decimal("10000")
    assert all(Decimal(row["btc_units"]) == 0 for row in foundation["balances"])
    path = ROOT / "var/operational.sqlite3"
    audit = audit_paper(path)
    public, restart = read("public-demo.json"), read("restart-demo.json")
    assert public["cycles_completed"] == public["accepted_quotes"] == 8
    assert public["new_fills"] == 1 and not public["running"]
    assert public["market_failures"] == public["feature_failures"] == 0
    assert restart["delivery"] == [] and restart["audit"] == audit
    files = [json.loads(file.read_text(encoding="utf-8")) for file in (OUT / "inbox").glob("*.json")]
    assert len({row["id"] for row in files}) == len(files)
    by_id = {row["id"]: row for row in files}
    with connection(path) as database:
        fills = [dict(row) for row in database.execute("SELECT * FROM paper_fills ORDER BY occurred_at,id")]
        messages = [dict(row) for row in database.execute("SELECT * FROM notification_outbox ORDER BY created_at,id")]
        advice = [dict(row) for row in database.execute("SELECT * FROM adviser_records ORDER BY attempted_at,id")]
        for fill in fills:
            payload = by_id["fill:" + fill["id"]]
            for key, value in fill.items():
                assert payload["fill"][key] == value
            assert payload["delivery"] == "local_demo"
        for row in messages:
            assert row["status"] == "delivered" and row["attempts"] == 1
            saved = by_id[row["id"]]
            assert saved["channel"] == row["channel"]
            payload = json.loads(row["payload_json"])
            assert all(saved[key] == value for key, value in payload.items())
        weekly = next(row for row in files if row["kind"] == "weekly_report")
        metrics = weekly["metrics"]
        start, end = metrics["period_start"], metrics["period_end"]
        marked = database.execute("SELECT * FROM paper_marks WHERE observed_at<=? ORDER BY observed_at,quote_id", (end,)).fetchall()
        window = [row for row in marked if row["observed_at"] > start]
        earlier = [row for row in marked if row["observed_at"] <= start]
        opening = Decimal(earlier[-1]["equity_usd"]) if earlier else Decimal("10000")
        closing = Decimal(marked[-1]["equity_usd"])
        assert Decimal(metrics["net_return_fraction"]) == closing / opening - 1
        peak, drawdown = opening, Decimal(0)
        for mark in window:
            value = Decimal(mark["equity_usd"])
            peak = max(peak, value)
            drawdown = max(drawdown, 1 - value / peak)
        assert Decimal(metrics["observed_max_drawdown_fraction"]) == drawdown
        in_period = [fill for fill in fills if start < fill["occurred_at"] <= end]
        assert len(in_period) == metrics["fills"] == 1
        assert sum(Decimal(fill["fee_usd"]) for fill in in_period) == Decimal(metrics["fees_usd"])
        assert datetime.fromisoformat(end).weekday() == 0 and datetime.fromisoformat(end).hour == 6
        assert datetime.fromisoformat(end) - datetime.fromisoformat(start) == timedelta(days=7)
        assert metrics["closing_mark_stale"] and "Partial period" in metrics["coverage"]
    tests = read("test-results.json")
    assert tests["tests_run"] == 78 and tests["failures"] == tests["errors"] == 0
    result = {"verified_at": datetime.now(timezone.utc).isoformat(), "python": platform.python_version(), "passed": True,
              "tests": tests, "archived_data_sha256": raw_hash, "foundation_portfolio_preserved": True,
              "native_sheet_values_match_local_controls": True, "native_sheet_url": native["url"],
              "local_messages": len(files), "fill_alerts": len(fills), "weekly_reports": 1,
              "restart_duplicate_messages": 0, "weekly_return_independently_reconciled": True,
              "weekly_drawdown_independently_reconciled": True, "weekly_period_excludes_later_fill": True,
              "audit": audit, "portfolio": paper_status(path), "shadow_records": len(advice),
              "actual_llm_calls": 0, "external_messages_sent": 0, "runtime_google_oauth_verified": False,
              "service_running": public["running"]}
    (OUT / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (OUT / "adviser-records.json").write_text(json.dumps([{**row, "context": json.loads(row["context_json"]),
              "advice": json.loads(row["advice_json"]) if row["advice_json"] else None, "applied_to_trades": False}
              for row in advice], indent=2), encoding="utf-8")
    text = weekly["text"]
    (OUT / "weekly-report.md").write_text("# Your Monday paper report\n\nThis is the frozen local email preview.\n\n```text\n" + text + "\n```\n", encoding="utf-8")
    review = f'''# Milestone 4 review

Completed on 2026-10-12. The owner can now review a local demo inbox without email or Telegram accounts. A private Google Sheet holds the initial nonsecret strategy controls. Configuration validation/cache, live adapter code and a bounded shadow adviser are implemented. The live provider paths remain unconfigured and were tested with mocks.

## Open the deliverables

- [Your local inbox](inbox/index.html): two paper trade alerts and one Monday report.
- [Monday report text](weekly-report.md): period-filtered saved portfolio figures.
- [Private configuration Sheet]({native['url']}): 16 supported controls and the local risk limits.
- [Integration setup and methodology](../../docs/integrations.md): how configuration, advice, recovery and reporting work.
- [Verification record](verification.json), [test record](test-results.json), [native Sheet readback](sheet-readback.json) and [shadow record](adviser-records.json).

## What was verified

All 78 automated tests passed. Integration cases cover bad/duplicate/incomplete Sheet values, native numeric conversion, cached restart, atomic write failure, fixed risk/funding bounds, retained inventory and existing stops, reservations canceled on updates, independent delivery recovery, uncertainty after interrupted network delivery, weekly time boundaries, future-fill exclusion, stale/partial coverage, daily adviser quotas, malformed/refused output and quote ticks while an integration is blocked.

The bounded [public-data run](public-demo.json) accepted eight quotes and made one new paper DCA fill. Feed and feature failures were zero. The new alert reached the local inbox. The [restart demo](restart-demo.json) created no duplicate delivery or trade. These short checks demonstrate plumbing and persistence, not seven days of reliability or profitable investing.

The operational ledger now has two fills, ${audit['reconstructed_cash_usd']} cash and {audit['inventory']['dca']} BTC. Total modeled fees are ${audit['fees_usd']}; independent cash difference is ${audit['cash_difference_usd']}. The last saved marked equity is ${Decimal(result['portfolio']['equity_usd']):.2f}; it is an observation from the bounded run, not a current live value. The original foundation example still holds its initial $10,000 and zero BTC. Archived research data retains its original SHA256.

The Monday report ends at 2026-10-12 09:00 Riyadh (06:00 UTC), before today's later demo trade. It therefore contains **one** fill and closes at ${closing:.2f}. Independent calculations reproduce its return and observed drawdown exactly. It labels a partial week and an old closing price. The later trade alert belongs to the next report period.

The native Google Sheet and the local inbox were visually inspected in the browser. The trade cards and full weekly report were readable. The temporary local preview server was stopped after inspection. The saved HTML and Markdown remain available without that server.

## How authority is limited

Sheet edits cannot change funding or core risk limits. Accepted changes preserve inventory and lifetime state and do not widen an existing swing stop. Invalid edits retain accepted configuration; a valid local baseline is the final fallback. The demo reads the verified initial local controls, while automatic private Sheet reads need separate Python OAuth.

The adviser is shadow-only. One record came from `deterministic-demo-fixture`, explicitly labeled as a test fixture. No actual LLM call or order influence occurred. There is no evidence here of LLM return improvement. Paid provider requests require account-specific credentials/model selection and spending limits; attempts/spacing/payload/output size are bounded locally.

Delivery failures do not create another trade. Local preview IDs survive restart. Live timeouts may be ambiguous and are held for inspection, because provider delivery cannot be guaranteed exactly once. Real email/Telegram delivery and OAuth have not been verified. No external messages were sent and no service remains running.

## Review stop

Milestone 4 implementation and the requested fake inbox demo are complete. Milestone 5 remains pending: Docker/service deployment, monitoring and **seven actual consecutive days** of observed paper operation. Historical replay and constructed stress cases will not count as those seven days. Wait for the owner's next **continue** before beginning that work.
'''
    (OUT / "review.md").write_text(review, encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("passed", "local_messages", "fill_alerts", "restart_duplicate_messages", "actual_llm_calls", "external_messages_sent", "service_running")}))


if __name__ == "__main__":
    verify()
