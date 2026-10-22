"""Capture point-in-time deployment evidence without changing the running policy."""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from bitcoin_agent.config import load_config
from bitcoin_agent.forward import audit_forward, forward_summary, source_checksum
from bitcoin_agent.notifications import render_inbox
from bitcoin_agent.paper_store import connection, paper_status
from bitcoin_agent.runtime import backup_database, runtime_health
from bitcoin_agent.storage import read_status

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "var/forward"
OUT = ROOT / "reports/milestone-5"


def main():
    now = datetime.now(timezone.utc)
    OUT.mkdir(parents=True, exist_ok=True)
    health = runtime_health(RUNTIME, now)
    assert health["healthy"], health["reasons"]
    assert health["supervisor"]["child_pid"] == health["worker"]["pid"]
    assert health["supervisor"]["restarts"] >= 1
    original = json.loads((RUNTIME / "recovery-check-before.json").read_text(encoding="utf-8-sig"))
    assert original["interrupted_worker_pid"] != health["worker"]["pid"]
    assert original["supervisor_pid"] == health["supervisor"]["pid"]
    destination = RUNTIME / "backups" / ("verified-" + now.strftime("%Y%m%dT%H%M%S%f") + ".sqlite3")
    backup = backup_database(RUNTIME / "ledger.sqlite3", destination)
    audit = audit_forward(destination)
    assert audit == backup["audit"]
    # In this startup/recovery check, no next daily tranche should be due.
    assert audit == original["audit_before"]
    summary = forward_summary(destination, now)
    assert summary["manifest"]["code_checksum"] == source_checksum()
    assert summary["manifest"]["config_checksum"] == load_config(ROOT / "config/forward.json").checksum
    assert not summary["seven_day_evidence_complete"] and summary["completed_days"] == 0
    assert summary["changed_config_checksums"] == [] and summary["observer_failures"] == 0
    with connection(destination) as database:
        accepted = database.execute("SELECT COUNT(*) FROM paper_quotes WHERE accepted=1").fetchone()[0]
        forward = database.execute("SELECT COUNT(*) FROM forward_observations").fetchone()[0]
        outbox = [dict(row) for row in database.execute("SELECT id,channel,status,attempts FROM notification_outbox ORDER BY created_at,id")]
        assert len(outbox) == 1 and outbox[0]["channel"] == "telegram" and outbox[0]["status"] == "delivered" and outbox[0]["attempts"] == 1
        joins = database.execute("SELECT COUNT(*) FROM forward_observations f JOIN paper_quotes p ON f.quote_id=p.id WHERE p.accepted!=1").fetchone()[0]
        assert joins == 0
    assert accepted == forward, "A candidate tick lacks its paired comparison observation; investigate before certifying evidence"
    assert (ROOT / "var/forward-startup-check-20261016/ledger.sqlite3").is_file()
    foundation = read_status(ROOT / "var/paper.sqlite3")
    assert foundation["event_count"] == 1 and all(row["btc_units"] == "0" for row in foundation["balances"])
    old = paper_status(ROOT / "var/operational.sqlite3")
    assert old["cash_usd"] == "9800.06" and old["btc_units"] == "0.00240330" and old["fills"] == 2
    raw_hash = hashlib.sha256((ROOT / "data/raw/btc_usd_1h.csv").read_bytes()).hexdigest()
    assert raw_hash == "31ae7877085693949570f3d343060c3340ffe8b292200001274b0816762c8080"
    render_inbox(destination, RUNTIME / "inbox")
    result = {"verified_at": now.isoformat(), "deployment_checkpoint_passed": True, "full_milestone_complete": False,
              "health": health, "initial_simulated_cash_usd": "10000.00", "audit": audit,
              "worker_restart_verified": True, "same_cash_inventory_fees_and_fill_count_after_restart": True,
              "no_duplicate_trade_alert": True, "paired_forward_observations": forward, "accepted_candidate_quotes": accepted,
              "backup": backup, "forward_status": summary["status"], "started_at": summary["started_at"], "window_ends_at": summary["window_ends_at"],
              "completed_days": summary["completed_days"], "qualifying_days": summary["qualifying_days"], "observer_failures": 0,
              "task_name": "BitcoinPaperAgent-01a129e0", "task_registration_and_start_observed": True,
              "docker_installed": False, "container_build_verified": False, "cloud_deployed": False,
              "external_messages_sent": 0, "llm_calls": 0, "prior_demo_preserved": True, "research_data_sha256": raw_hash,
              "first_short_startup_check_preserved": "var/forward-startup-check-20261016"}
    (OUT / "verification.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    (OUT / "evaluation-at-checkpoint.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    local = timezone(timedelta(hours=3), "Asia/Riyadh")
    start = datetime.fromisoformat(summary["started_at"]).astimezone(local)
    end = datetime.fromisoformat(summary["window_ends_at"]).astimezone(local)
    review = f'''# Milestone 5 deployment checkpoint

Local deployment is running. The seven-day evaluation is collecting prospective data, so **the full milestone remains incomplete**.

The owner chose their Windows computer. The per-user limited task `BitcoinPaperAgent-01a129e0` runs a supervised, paper-only worker independently of this chat and resumes at sign-in. Keep the computer awake, signed in and online. No power-plan or sleep-protection setting was changed.

## Review the current state

- [Updating evaluation report](../../var/forward/reports/evaluation.md)
- [Local paper-alert inbox](../../var/forward/inbox/index.html)
- [Deployment controls and methodology](../../docs/deployment.md)
- [Verification snapshot](verification.json) and [98-test record](test-results.json)

The run began **{start:%Y-%m-%d %H:%M:%S} Asia/Riyadh**. Its boundary is **{end:%Y-%m-%d %H:%M:%S} Asia/Riyadh**. At this checkpoint, completed days are **0/7**. Only actual public quotes count; the short earlier demos, indicator-warming candles, constructed tests and offline elapsed time do not fill the week.

## Evidence captured

All 98 automated tests passed, including cash/inventory conservation for baseline books, duplicate/restart protection, source/config/environment freeze, quote-age coverage, missing days, backup restoration and actual Windows worker PID/library loading. A constructed seven-day test verifies the coverage calculation and is explicitly separate from this live run.

The live supervisor owns the actual worker process. One guarded interruption stopped that worker after verifying its parent, token and ledger path. The supervisor restarted it automatically. Candidate cash, BTC, fees and the single fill stayed exactly unchanged through the interruption, and the local alert remained a single delivery. Fresh quotes and features resumed, and the observation log records the resulting gap.

A consistent online SQLite backup was independently audited and compared with the pre-interruption account. The candidate and all three baseline books reconcile. At the snapshot, all **{forward}** accepted candidate quotes had paired forward observations. The old foundation account, milestone-4 demo and raw research checksum remain unchanged.

The timed account uses $10,000 simulated funds and frozen defensive rules. Scheduled DCA, buy-and-hold and cash books receive the same observations and modeled costs. Google edits and LLM advice are disabled in this run; notifications are local. No funded trade, external message or paid model call occurred.

The first brief Windows startup check exposed a venv-launcher extra process. It was stopped and archived separately before the timed run, and the monitored child now matches the actual worker PID. That short check is not counted toward the week.

## What remains

Wait for the full observed window, inspect each day's declared 99% quote-age coverage and integrity criteria, reconcile ledgers/backups, and review incidents and comparisons. The program closes the window and stops after seven elapsed days; insufficient coverage will be reported as a failed criterion, not hidden or presented as a completed reliable week. Seven days cannot establish durable investment returns.

Docker/Compose packaging is prepared but unbuilt because Docker is absent. No cloud deployment was performed. The chosen Windows deployment is the verified runtime for this checkpoint.

Implementation stops here for owner review while the already authorized collector continues. Milestone 6 remains pending.
'''
    (OUT / "review.md").write_text(review, encoding="utf-8")
    print(json.dumps({key: result[key] for key in ("deployment_checkpoint_passed", "full_milestone_complete", "worker_restart_verified", "paired_forward_observations", "started_at", "window_ends_at", "completed_days")}))


if __name__ == "__main__":
    main()
