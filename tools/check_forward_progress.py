"""Audit local forward progress without changing the frozen policy or resetting data."""

import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from bitcoin_agent.config import load_config
from bitcoin_agent.forward import audit_forward, environment_versions, forward_summary, source_checksum
from bitcoin_agent.paper_store import connection
from bitcoin_agent.runtime import runtime_health


def pairing(path):
    with connection(path) as database:
        database.execute("BEGIN")
        return {
            "accepted_quotes": database.execute("SELECT COUNT(*) FROM paper_quotes WHERE accepted=1").fetchone()[0],
            "forward_observations": database.execute("SELECT COUNT(*) FROM forward_observations").fetchone()[0],
            "unpaired_accepted_quotes": database.execute("SELECT COUNT(*) FROM paper_quotes q LEFT JOIN forward_observations o ON o.quote_id=q.id WHERE q.accepted=1 AND o.quote_id IS NULL").fetchone()[0],
        }


def main():
    runtime = ROOT / "var/forward"
    state_path = ROOT / "var/completion/state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    path = runtime / "ledger.sqlite3"
    summary = forward_summary(path)
    health = runtime_health(runtime)
    audit = audit_forward(path)
    pairs = pairing(path)
    # Candidate and observer commit separately. Retry a momentary in-flight tick.
    for _ in range(3):
        if not pairs["unpaired_accepted_quotes"]:
            break
        time.sleep(.25)
        pairs = pairing(path)
    manifest = summary["manifest"]
    submission = json.loads((ROOT / "config/submission.json").read_text(encoding="utf-8"))
    cleanly_closed = (runtime / "COMPLETE").exists() and summary["status"] == "seven_day_window_closed" \
        and (health.get("supervisor") or {}).get("state") == (health.get("worker") or {}).get("state") == "stopped"
    daily_budget = (1 - manifest["minimum_daily_quote_coverage"]) * 86400
    daily = []
    for day in summary["days"]:
        elapsed = (datetime.fromisoformat(day["end"]) - datetime.fromisoformat(day["start"])).total_seconds()
        uncovered = max(0, elapsed * (1 - day["quote_coverage_fraction"]))
        daily.append({"day": day["day"], "uncovered_seconds_so_far": uncovered,
                      "full_day_uncovered_allowance_seconds": daily_budget,
                      "full_day_criterion_still_achievable": uncovered <= daily_budget + .000001,
                      "completed_day_failed": day["complete_24_hours"] and not day["qualifies"]})
    checks = {
        "collector_running_or_cleanly_completed": health["healthy"] or cleanly_closed,
        "ledger_reconciles": audit["passed"] and audit["baseline_books_reconciled"] == 3,
        "source_frozen": source_checksum() == manifest["code_checksum"],
        "configuration_frozen": load_config(ROOT / "config/forward.json").checksum == manifest["config_checksum"],
        "environment_frozen": environment_versions() == manifest["environment"],
        "no_observer_failures": summary["observer_failures"] == 0,
        "no_config_changes": summary["changed_config_checksums"] == [],
        "all_accepted_quotes_paired": pairs["unpaired_accepted_quotes"] == 0 and pairs["accepted_quotes"] == pairs["forward_observations"],
        "original_and_rehearsal_preserved": all((ROOT / name).is_dir() for name in (
            "var/archive/forward-20261016-interrupted", "var/archive/forward-20261019-recovery-rehearsal")),
        "uploads_disabled": submission["uploads_authorized"] is False,
        "youtube_not_required": submission["youtube_required"] is False,
        "coverage_window_still_eligible": all(row["full_day_criterion_still_achievable"] and not row["completed_day_failed"] for row in daily),
    }
    backups = sorted((runtime / "backups").glob("*.sqlite3"))
    checks["verified_backup_available"] = bool(backups)
    backup_details = None
    if backups:
        backup = backups[-1]
        metadata = json.loads(backup.with_suffix(backup.suffix + ".json").read_text(encoding="utf-8"))
        checks["latest_backup_hash_valid"] = hashlib.sha256(backup.read_bytes()).hexdigest() == metadata["sha256"]
        checks["latest_backup_audit_valid"] = audit_forward(backup) == metadata["audit"]
        backup_details = {"file": backup.name, "created_at": metadata["created_at"]}
    now = datetime.now(timezone.utc)
    prior = state.get("last_check", {})
    report = {
        "checked_at": now.isoformat(), "checks": checks, "healthy": health["healthy"],
        "cleanly_closed": cleanly_closed, "health_reasons": health["reasons"],
        "supervisor_pid": (health.get("supervisor") or {}).get("pid"),
        "supervisor_restarts": (health.get("supervisor") or {}).get("restarts"),
        "worker_pid": (health.get("worker") or {}).get("pid"),
        "previous_worker_pid": prior.get("worker_pid"), "pairing": pairs,
        "completed_days": summary["completed_days"], "qualifying_days": summary["qualifying_days"],
        "observations": summary["observations"], "quote_coverage_fraction": summary["quote_coverage_fraction"],
        "max_observation_gap_seconds": summary["max_observation_gap_seconds"],
        "last_observation_at": summary["last_observation_at"], "latest_backup": backup_details,
        "days": summary["days"], "daily_coverage_budget": daily,
        "seven_day_evidence_complete": summary["seven_day_evidence_complete"],
        "meaningful_milestone": summary["qualifying_days"] > state.get("last_notified_qualifying_days", 0) or summary["seven_day_evidence_complete"],
        "action_required": not all(checks.values()),
    }
    folder = ROOT / "var/completion/checks"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / (now.strftime("%Y%m%dT%H%M%S") + ".json")).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    state.update(updated_at=now.isoformat(), health_at_last_check=health["healthy"],
                 completed_days=summary["completed_days"], qualifying_days=summary["qualifying_days"],
                 last_check=report, uploads_authorized=False, youtube_required=False)
    state_path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 1 if report["action_required"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
