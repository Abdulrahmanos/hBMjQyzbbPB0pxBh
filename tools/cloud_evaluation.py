"""Fresh cloud week and isolated OpenAI observer. No infrastructure provisioning."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import sqlite3
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Event

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bitcoin_agent.adviser import OpenAIAdviser, ShadowAdviser
from bitcoin_agent.config import load_config
from bitcoin_agent.forward import environment_versions, forward_summary, source_checksum
from bitcoin_agent.paper_store import connection, paper_status
from bitcoin_agent.paper_types import Features, utc
from bitcoin_agent.remote_config import atomic_json
from bitcoin_agent.runtime import ProcessLock, backup_database, now_utc, read_json, runtime_health, supervise

CONFIG = ROOT / "config/cloud-paper.json"
SETTINGS = ROOT / "config/integrations.cloud.json"
PROTOCOL = ROOT / "config/cloud-protocol.json"


def private_key(path):
    """Require an owner-supplied file. Never fall back to an inherited executor key."""
    values = []
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and key.strip() == "OPENAI_API_KEY":
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            values.append(value)
    if len(values) != 1 or not values[0] or any(c.isspace() or ord(c) < 32 for c in values[0]):
        raise ValueError("Save one nonempty OPENAI_API_KEY in the private file; its value is never displayed")
    return values[0]


def prepare_secret(source, destination):
    key = private_key(source)
    destination = Path(destination).resolve()
    # Secrets may only be prepared under the ignored project var/secrets directory.
    allowed = (ROOT / "var/secrets").resolve()
    if not destination.is_relative_to(allowed):
        raise ValueError("Secret destination must be inside this project's ignored var/secrets")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as stream:
        stream.write("OPENAI_API_KEY=" + key + "\n")
    destination.chmod(0o600)
    return {"prepared": True, "path": str(destination.relative_to(ROOT)), "contains_only_openai_key": True}


def fingerprint():
    files = ["config/cloud-paper.json", "config/integrations.cloud.json",
             "config/cloud-protocol.json", "requirements-runtime.lock.txt",
             "tools/cloud_evaluation.py", "Dockerfile", "compose.evaluation.yaml"]
    return {"application_sha256": source_checksum(), "environment": environment_versions(),
            "files": {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in files},
            "protocol": json.loads(PROTOCOL.read_text(encoding="utf-8"))}


def freeze(runtime):
    runtime = Path(runtime)
    runtime.mkdir(parents=True, exist_ok=True)
    expected = fingerprint()
    path = runtime / "cloud-manifest.json"
    saved = read_json(path)
    if path.exists():
        if not saved or saved.get("fingerprint") != expected:
            raise ValueError("Cloud source, settings or environment changed; preserve this experiment")
        return saved
    if (runtime / "ledger.sqlite3").exists():
        raise ValueError("Refusing to attach cloud protocol to an existing account")
    saved = {"run_id": uuid.uuid4().hex, "declared_at": now_utc().isoformat(),
             "fingerprint": expected, "clock_starts_on_first_accepted_quote": True,
             "historical_warmup_counts_as_evidence": False}
    atomic_json(path, saved)
    return saved


def bind_observer(runtime, state):
    saved = read_json(Path(runtime) / "cloud-manifest.json")
    if not saved:
        return None
    if saved.get("fingerprint") != fingerprint():
        raise ValueError("Observer does not match frozen cloud source")
    path = Path(state) / "observer-manifest.json"
    previous = read_json(path)
    if path.exists() and previous != saved:
        raise ValueError("Observer belongs to a different experiment; no state was reset")
    if not path.exists():
        atomic_json(path, saved)
    return saved


def observe_once(runtime, state, config, adviser, now=None):
    runtime, state, now = Path(runtime), Path(state), utc(now or now_utc())
    if (runtime / "STOP").exists() or (runtime / "COMPLETE").exists():
        return {"status": "stopped", "applied_to_trades": False}
    if not bind_observer(runtime, state):
        return {"status": "waiting_for_paper_manifest", "applied_to_trades": False}
    if not runtime_health(runtime, now)["healthy"]:
        return {"status": "waiting_for_fresh_paper_data", "applied_to_trades": False}
    # Read one committed portfolio snapshot; no write access to the paper volume.
    portfolio = paper_status(runtime / "ledger.sqlite3")
    age = (now - utc(portfolio["last_quote_at"])).total_seconds()
    if not -5 <= age <= config.data["market"]["stale_after_seconds"]:
        return {"status": "waiting_for_fresh_portfolio", "applied_to_trades": False}
    features = Features.from_dict(read_json(runtime / "market/features.json"))
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    with connection(adviser.path) as db:
        attempts = db.execute("SELECT COUNT(*) FROM adviser_records").fetchone()[0]
    if attempts >= protocol["maximum_attempts"]:
        return {"status": "total_attempt_budget_reached", "applied_to_trades": False}
    # Clear any prior receipt even on a skipped/idempotent observation.
    adviser.provider.last_receipt = None
    result = adviser.observe(config, features, portfolio, now)
    receipt = getattr(adviser.provider, "last_receipt", None)
    if result["status"] == "shadow_recorded" and receipt:
        atomic_json(state / "receipts" / (result["id"] + ".json"),
                    {"attempt_id": result["id"], "observed_at": now.isoformat(),
                     "requested_model": protocol["model"], "advice": result["advice"],
                     "applied_to_trades": False, **receipt})
        result["provider_receipt_verified"] = True
    return result


def run_adviser(runtime, state, secret_file):
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    os.environ["OPENAI_API_KEY"] = private_key(secret_file)
    os.environ["LLM_MODEL"] = protocol["model"]
    state = Path(state)
    stop = Event()
    for name in ("SIGTERM", "SIGINT"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    with ProcessLock(state / "adviser.lock"):
        provider = OpenAIAdviser()
        adviser = ShadowAdviser(state / "shadow.sqlite3", provider,
                                daily_calls=protocol["daily_attempts"],
                                interval_hours=protocol["interval_hours"])
        config = load_config(CONFIG)
        while not stop.is_set():
            try:
                result = observe_once(runtime, state, config, adviser)
            except (OSError, ValueError, KeyError, TypeError, ArithmeticError, sqlite3.Error):
                # No raw exception, response, context or environment goes to logs.
                result = {"status": "observer_error", "applied_to_trades": False}
            atomic_json(state / "status.json", {"checked_at": now_utc().isoformat(), **result})
            if result["status"] == "stopped":
                return
            stop.wait(60)


def export_review(runtime, state, output, require_complete=False):
    runtime, state, output = Path(runtime).resolve(), Path(state).resolve(), Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    stamp = now_utc().strftime("%Y%m%dT%H%M%S%f")
    snapshot = output / "snapshots" / (stamp + ".sqlite3")
    backup = backup_database(runtime / "ledger.sqlite3", snapshot)
    summary = forward_summary(snapshot)
    manifest = read_json(runtime / "cloud-manifest.json") or {}
    with connection(snapshot) as db:
        missing = db.execute("SELECT COUNT(*) FROM paper_quotes q LEFT JOIN forward_observations o ON o.quote_id=q.id WHERE q.accepted=1 AND o.quote_id IS NULL").fetchone()[0]
        extra = db.execute("SELECT COUNT(*) FROM forward_observations o LEFT JOIN paper_quotes q ON q.id=o.quote_id WHERE q.id IS NULL OR q.accepted!=1").fetchone()[0]
    worker, supervisor = read_json(runtime / "worker.json") or {}, read_json(runtime / "supervisor.json") or {}
    receipts = []
    attempts = successes = 0
    shadow = state / "shadow.sqlite3"
    if shadow.exists():
        with connection(shadow) as db:
            rows = {row["id"]: dict(row) for row in db.execute("SELECT * FROM adviser_records")}
        attempts = len(rows)
        successes = sum(row["status"] == "shadow_recorded" for row in rows.values())
        for path in sorted((state / "receipts").glob("*.json")):
            receipt = read_json(path)
            row = rows.get(path.stem)
            if (receipt and row and row["status"] == "shadow_recorded"
                    and receipt.get("attempt_id") == row["id"]
                    and receipt.get("advice") == json.loads(row["advice_json"])
                    and receipt.get("applied_to_trades") is False
                    and receipt.get("response_id") and receipt.get("returned_model")):
                receipts.append(receipt)
        # Preserve a consistent shadow evidence snapshot as well.
        target = output / "snapshots" / (stamp + "-shadow.sqlite3")
        with sqlite3.connect(shadow.as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(target) as dest:
            source.backup(dest)
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    previous_backups = []
    for path in (runtime / "backups").glob("*.sqlite3"):
        metadata = read_json(path.with_suffix(".sqlite3.json")) or {}
        if (metadata.get("sha256") == hashlib.sha256(path.read_bytes()).hexdigest()
                and metadata.get("audit", {}).get("passed")):
            previous_backups.append(path.name)
    checks = {
        "seven_prospective_days_pass": summary["seven_day_evidence_complete"] is True
            and len(summary.get("days", [])) == 7 and all(row["qualifies"] and row["complete_24_hours"] for row in summary["days"]),
        "candidate_and_three_baselines_reconcile": backup["audit"].get("passed") is True
            and backup["audit"].get("baseline_books_reconciled") == 3 and backup["audit"].get("candidate", {}).get("passed") is True,
        "all_accepted_quotes_paired": missing == extra == 0,
        "source_config_environment_frozen": manifest.get("fingerprint") == fingerprint()
            and summary["manifest"].get("code_checksum") == source_checksum()
            and summary["manifest"].get("config_checksum") == load_config(CONFIG).checksum
            and summary["manifest"].get("environment") == environment_versions(),
        "prospective_public_quotes_only": summary["manifest"].get("source") == "coinbase_public_stream"
            and summary["manifest"].get("historical_candles_count_as_forward") is False,
        "no_observer_or_configuration_failure": summary.get("observer_failures") == 0 and summary.get("changed_config_checksums") == [],
        "window_closed_and_collectors_stopped": (runtime / "COMPLETE").exists()
            and worker.get("state") == supervisor.get("state") == "stopped",
        "independent_backups_available": bool(previous_backups),
        "real_openai_response_recorded": bool(receipts),
        "observer_bound_to_this_run": read_json(state / "observer-manifest.json") == manifest and bool(manifest),
        "attempt_budget_respected": attempts <= protocol["maximum_attempts"],
    }
    verified = all(checks.values())
    report = {"generated_at": now_utc().isoformat(), "complete": verified, "checks": checks,
              "evaluation": summary, "ledger_audit": backup["audit"],
              "snapshot_sha256": backup["sha256"], "verified_backup_count": len(previous_backups),
              "llm": {"attempts": attempts, "validated_suggestions": successes,
                      "provider_receipts": len(receipts), "applied_to_trades": False,
                      "profit_improvement_tested": False, "requested_model": protocol["model"],
                      "model_weights_immutability_guaranteed": False}}
    atomic_json(output / "verification.json", report)
    atomic_json(output / "openai-receipts.json", receipts)
    lines = ["# Cloud paper week", "", "Status: **" + ("verified completed week" if verified else "incomplete") + "**", "",
             f"Actual first quote: {summary.get('started_at', 'not received')}. Boundary: {summary.get('window_ends_at', 'not started')}.", "",
             f"Qualifying days: {summary['qualifying_days']}/7. Observations: {summary['observations']}.", "",
             "| Policy | Equity USD | Net return | Observed max drawdown |",
             "| --- | ---: | ---: | ---: |"]
    for name, row in summary.get("strategies", {}).items():
        lines.append(f"| {name} | {float(row['equity_usd']):.2f} | {float(row['net_return_fraction']):.2%} | {float(row['observed_max_drawdown_fraction']):.2%} |")
    lines += ["", f"OpenAI attempts: {attempts}; validated advice: {successes}; matching provider receipts: {len(receipts)}.",
              "LLM suggestions were recorded separately and did not affect any trade. This verifies real model use, not improved returns. The requested model name is frozen; the provider may update underlying weights.",
              "", "Only observed prospective quotes count; historical warm-up never fills gaps. The original 99% daily coverage protocol remains. Seven days cannot establish durable profitability.", "",
              "Swing remains disabled under the existing operational policy. This week is separate from the historical LSTM comparison and earlier interrupted accounts.", ""]
    lines += [f"- {'PASS' if value else 'PENDING / FAIL'}: {key.replace('_', ' ')}" for key, value in checks.items()]
    (output / "review.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    if require_complete and not verified:
        raise ValueError("Finalization withheld; inspect the recorded failed checks")
    return {"complete": verified, "checks": checks, "output": str(output)}



def probe_llm(secret_file, output):
    """A real current-feature API demonstration, separate from prospective evidence."""
    from bitcoin_agent.paper_service import refresh_features
    from bitcoin_agent.paper_store import initialize_paper
    protocol = json.loads(PROTOCOL.read_text(encoding="utf-8"))
    os.environ["OPENAI_API_KEY"] = private_key(secret_file)
    os.environ["LLM_MODEL"] = protocol["model"]
    output = Path(output).resolve()
    if not output.is_relative_to((ROOT / "var").resolve()):
        raise ValueError("LLM demonstration output must stay in ignored var")
    output.mkdir(parents=True, exist_ok=True)
    config, at = load_config(CONFIG), now_utc()
    features = refresh_features(config, ROOT / "data/raw/btc_usd_1h.csv", output / "market", at)
    path = output / "demonstration.sqlite3"
    initialize_paper(path, config)
    provider = OpenAIAdviser()
    adviser = ShadowAdviser(path, provider)
    result = adviser.observe(config, features, paper_status(path), now_utc())
    receipt = provider.last_receipt
    proof = {"observed_at": now_utc().isoformat(), "demonstration_only": True,
             "counts_as_week_evidence": False, "provider_response_verified": bool(receipt),
             "profit_improvement_tested": False, **result, "receipt": receipt}
    atomic_json(output / "verification.json", proof)
    return {"status": result["status"], "provider_response_verified": bool(receipt),
            "counts_as_week_evidence": False, "output": str(output)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare-secret", "probe", "paper", "adviser", "health", "export"])
    parser.add_argument("--runtime", default="var/cloud-week")
    parser.add_argument("--state", default="var/cloud-adviser")
    parser.add_argument("--secret-file", default="/run/secrets/openai")
    parser.add_argument("--source", default=".env")
    parser.add_argument("--destination", default="var/secrets/cloud-openai.env")
    parser.add_argument("--output", default="var/cloud-adviser/reports")
    parser.add_argument("--require-complete", action="store_true")
    args = parser.parse_args()
    try:
        if args.action == "prepare-secret":
            result = prepare_secret(args.source, args.destination)
        elif args.action == "probe":
            result = probe_llm(args.secret_file, args.output)
        elif args.action == "paper":
            with ProcessLock(Path(args.runtime) / "cloud-launch.lock"):
                freeze(args.runtime)
                result = supervise(CONFIG, Path(args.runtime) / "ledger.sqlite3", args.runtime, SETTINGS)
        elif args.action == "adviser":
            run_adviser(args.runtime, args.state, args.secret_file)
            result = {"stopped": True}
        elif args.action == "health":
            if (Path(args.runtime) / "COMPLETE").exists():
                result = {"window_elapsed": True, "validation_passed": False,
                          "note": "Window elapsed is not a validation pass; export independently"}
            else:
                result = runtime_health(args.runtime)
                if not result["healthy"]:
                    print(json.dumps({"healthy": False, "reasons": result["reasons"]}))
                    return 2
        else:
            result = export_review(args.runtime, args.state, args.output, args.require_complete)
        print(json.dumps(result))
        return 0
    except (OSError, ValueError, ArithmeticError, sqlite3.Error):
        print("Cloud operation could not complete; check private setup and recorded status. No secret details displayed.", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
