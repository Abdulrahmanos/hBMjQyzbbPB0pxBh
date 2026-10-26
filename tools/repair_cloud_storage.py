"""Repair read-only observer access without changing a frozen trading program.

Run only inside the paper container's writable volume. SQLite changes its
storage journal through its supported API; no order or accounting row is edited.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
import uuid
from contextlib import closing
from pathlib import Path

ROOT = Path.cwd().resolve()
sys.path.insert(0, str(ROOT))
from bitcoin_agent.forward import audit_forward
from bitcoin_agent.paper_store import connection
from bitcoin_agent.runtime import ProcessLock, backup_database, now_utc, read_json
from bitcoin_agent.remote_config import atomic_json
from tools import cloud_evaluation as cloud


def account_identity(path):
    with connection(path) as db:
        return db.execute("SELECT manifest_json FROM forward_run WHERE id=1").fetchone()[0]


def journal_mode(path):
    with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as db:
        return db.execute("PRAGMA journal_mode").fetchone()[0].lower()


def convert_journal(path):
    """SQLite performs the conversion and locking. Never manipulate WAL files."""
    for attempt in range(20):
        try:
            with closing(sqlite3.connect(Path(path).resolve().as_uri() + "?mode=rw",
                                        uri=True, timeout=1, isolation_level=None)) as db:
                mode = db.execute("PRAGMA journal_mode=DELETE").fetchone()[0].lower()
                if mode != "delete":
                    raise ValueError("SQLite did not accept rollback journaling; preserve the run")
                if db.total_changes != 0:
                    raise ArithmeticError("Unexpected row changes during storage maintenance")
                if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                    raise ArithmeticError("Database integrity check failed")
                return mode
        except sqlite3.OperationalError as exc:
            if (getattr(exc, "sqlite_errorcode", -1) & 255) != sqlite3.SQLITE_BUSY or attempt == 19:
                raise
            time.sleep(0.25)
    raise ArithmeticError("Storage maintenance did not complete")


def repair(runtime, output, *, apply=False):
    runtime, output = Path(runtime).resolve(), Path(output).resolve()
    path = runtime / "ledger.sqlite3"
    if not path.is_file():
        raise ValueError("Existing paper ledger required; no account will be created")
    if not output.is_relative_to((cloud.ROOT / "var").resolve()) or output == runtime:
        raise ValueError("Maintenance evidence must stay in a distinct ignored var directory")
    manifest_path = runtime / "cloud-manifest.json"
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get("fingerprint") != cloud.fingerprint():
        raise ValueError("Frozen program does not match; no repair was applied")
    if any((runtime / name).exists() for name in ("STOP", "COMPLETE")):
        raise ValueError("This helper only repairs an active run; preserve closed evidence")
    identity = account_identity(path)
    mode = journal_mode(path)
    if mode not in {"wal", "delete"}:
        raise ValueError("Unexpected journal mode; no repair was applied")
    preview = {"run_id": manifest["run_id"], "journal_mode": mode,
               "planned_journal_mode": "delete", "apply": apply,
               "changes_trading_rules": False, "resets_account": False}
    if not apply:
        return preview
    output.mkdir(parents=True, exist_ok=True)
    with ProcessLock(output / "storage-repair.lock"):
        stamp = now_utc().strftime("%Y%m%dT%H%M%S%f") + "-" + uuid.uuid4().hex[:8]
        backup = backup_database(path, output / "snapshots" / (stamp + ".sqlite3"))
        proof = {**preview, "started_at": now_utc().isoformat(), "status": "started",
                 "backup": backup, "sql_rows_changed_by_repair": 0,
                 "read_only_container_access_verified": False,
                 "note": "Storage intervention during the existing run; original identity and deadline are retained. No model request is made."}
        proof_path = output / (stamp + ".json")
        atomic_json(proof_path, proof)
        try:
            proof["journal_mode_after"] = convert_journal(path)
            proof["account_identity_preserved"] = account_identity(path) == identity
            proof["frozen_manifest_preserved"] = manifest_path.read_bytes() == manifest_bytes
            proof["frozen_program_matches"] = manifest["fingerprint"] == cloud.fingerprint()
            proof["audit_after"] = audit_forward(path)
            if not all(proof[key] for key in ("account_identity_preserved",
                                             "frozen_manifest_preserved", "frozen_program_matches")):
                raise ArithmeticError("Identity or freeze verification failed")
            proof["status"] = "repaired"
        except (OSError, ValueError, ArithmeticError, sqlite3.Error) as exc:
            proof.update(status="failed", error_type=type(exc).__name__,
                         sqlite_error_name=getattr(exc, "sqlite_errorname", None))
            atomic_json(proof_path, proof)
            raise
        proof["finished_at"] = now_utc().isoformat()
        atomic_json(proof_path, proof)
        atomic_json(output / "latest.json", proof)
        return {"status": proof["status"], "journal_mode_before": mode,
                "journal_mode_after": proof["journal_mode_after"],
                "ledger_audit_passed": proof["audit_after"]["passed"],
                "baseline_books_reconciled": proof["audit_after"]["baseline_books_reconciled"],
                "frozen_manifest_preserved": proof["frozen_manifest_preserved"],
                "account_identity_preserved": proof["account_identity_preserved"],
                "sql_rows_changed_by_repair": 0, "proof": str(proof_path)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", default="var/cloud-week")
    parser.add_argument("--output", default="var/cloud-maintenance/storage")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    try:
        print(json.dumps(repair(args.runtime, args.output, apply=args.apply), indent=2))
        return 0
    except (OSError, ValueError, ArithmeticError, sqlite3.Error) as exc:
        print("Storage repair stopped: " + type(exc).__name__ + "; no account was reset.", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
