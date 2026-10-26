"""Separate Gmail worker: read frozen paper evidence, write only its own outbox."""
from __future__ import annotations

import argparse
import json
import re
import signal
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bitcoin_agent.notifications import (
    GmailSender, dispatch, enqueue, initialize_outbox, outbox_status,
    report_boundary, weekly_report,
)
from bitcoin_agent.paper_store import connection
from bitcoin_agent.paper_types import utc
from bitcoin_agent.remote_config import atomic_json
from bitcoin_agent.runtime import ProcessLock
from tools.cloud_evaluation import fingerprint

SCHEDULE = {"timezone": "Asia/Riyadh", "weekly_day": "monday", "weekly_time": "09:00"}


def bind(runtime, state):
    runtime, state = Path(runtime).resolve(), Path(state).resolve()
    if state == runtime or state.is_relative_to(runtime) or runtime.is_relative_to(state):
        raise ValueError("Mailer state must be separate from paper evidence")
    manifest = json.loads((runtime / "cloud-manifest.json").read_text(encoding="utf-8"))
    if not re.fullmatch(r"[a-f0-9]{32}", manifest.get("run_id", "")):
        raise ValueError("Invalid cloud run identity")
    if manifest.get("fingerprint") != fingerprint():
        raise ValueError("Frozen paper source/configuration/environment mismatch")
    bound = state / "bound-manifest.json"
    if bound.exists() and json.loads(bound.read_text(encoding="utf-8")) != manifest:
        raise ValueError("Mailer belongs to a different run; preserve its outbox")
    if not bound.exists():
        atomic_json(bound, manifest)
    return manifest["run_id"]


def cycle(runtime, state, now, sender=None, *, test=False):
    runtime, state, now = Path(runtime), Path(state), utc(now)
    run_id = bind(runtime, state)
    ledger, outbox = runtime / "ledger.sqlite3", state / "outbox.sqlite3"
    initialize_outbox(outbox)
    end = report_boundary(now, SCHEDULE)
    with connection(ledger) as db:
        first = db.execute("SELECT MIN(observed_at) FROM paper_marks").fetchone()[0]
    identity = ("cloud-test:" + run_id) if test else ("weekly:" + run_id + ":" + end.isoformat())
    with connection(outbox) as db:
        existing = db.execute("SELECT 1 FROM notification_outbox WHERE id=?", (identity,)).fetchone()
    if not existing and (test or (first and utc(first) <= end)):
        if test:
            payload = {
                "subject": "Bitcoin PAPER: AWS Gmail connection verified",
                "text": "This is the AWS report sender's connection test. No real trade was placed. "
                        "Weekly reports are scheduled for Monday 09:00 Asia/Riyadh. "
                        "This email does not certify completion of the seven-day experiment.",
            }
        else:
            payload = weekly_report(ledger, end, now)
            payload["text"] += (
                "\n\nThis scheduled snapshot is separate from the final seven-day coverage and ledger audit. "
                "LLM suggestions are recorded in shadow mode and do not place trades."
            )
        with connection(outbox, write=True) as db:
            enqueue(db, identity, "gmail", now, payload)
    results = dispatch(outbox, {"gmail": sender or GmailSender()}, now, limit=5)
    rows = outbox_status(outbox)
    result = {
        "checked_at": now.isoformat(), "schedule": "Monday 09:00 Asia/Riyadh",
        "latest_due_boundary": end.isoformat(), "first_paper_observation": first,
        "latest_report_eligible": bool(first and utc(first) <= end),
        "delivery_results": results,
        "needs_attention": any(row["status"] in {"blocked", "uncertain"} for row in rows),
        "outbox": rows,
        "paper_access": "read_only", "seven_day_validation_certified": False,
    }
    atomic_json(state / "status.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["run", "once", "test", "status"])
    parser.add_argument("--runtime", default="/app/evidence/cloud-week")
    parser.add_argument("--state", default="/app/var/gmail")
    args = parser.parse_args()
    state = Path(args.state).resolve()
    runtime = Path(args.runtime).resolve()
    if state == runtime or state.is_relative_to(runtime) or runtime.is_relative_to(state):
        parser.error("Mailer state must be separate from paper evidence")
    if args.action == "status":
        print((state / "status.json").read_text(encoding="utf-8"))
        return 0
    stop = Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    with ProcessLock(state / "worker.lock"):
        while not stop.is_set():
            try:
                result = cycle(args.runtime, state, datetime.now(timezone.utc), test=args.action == "test")
            except Exception as exc:
                # Provider bodies, credentials and raw exceptions never enter logs.
                result = {"checked_at": datetime.now(timezone.utc).isoformat(),
                          "needs_attention": True, "error_type": type(exc).__name__,
                          "status": "report_worker_error"}
                atomic_json(state / "status.json", result)
            if args.action != "run":
                print(json.dumps(result, indent=2))
                if args.action == "test":
                    delivered = any(r["id"].startswith("cloud-test:") and r["status"] == "delivered"
                                    for r in result.get("outbox", []))
                    return 0 if delivered else 1
                return 1 if result.get("needs_attention") else 0
            # Quiet until a delivery or an actionable error. No chat automation.
            if result.get("delivery_results") or result.get("needs_attention"):
                print(json.dumps({k: result[k] for k in ("checked_at", "needs_attention", "error_type",
                                                       "status", "delivery_results") if k in result}), flush=True)
            stop.wait(30)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
