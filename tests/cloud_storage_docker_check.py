"""Synthetic Docker storage regression. Never prospective market evidence."""
import json
import sqlite3
import sys
from contextlib import closing
from datetime import timedelta
from pathlib import Path
sys.path.insert(0, "/app")
sys.path.insert(0, "/app/tests")
from bitcoin_agent.config import load_config
from bitcoin_agent.forward import ForwardEvaluation, audit_forward, forward_summary
from bitcoin_agent.paper_engine import PaperEngine
from bitcoin_agent.paper_store import paper_status
from tools import cloud_evaluation as cloud
from test_paper_engine import START, features, quote

action = sys.argv[1]
root = Path("/app/var/cloud-week")
path = root / "ledger.sqlite3"
if action in {"prepare", "update"}:
    config = load_config(cloud.CONFIG)
    cloud.freeze(root)
    engine = PaperEngine(path, config)
    evaluation = ForwardEvaluation(path, config)
    now = START if action == "prepare" else START + timedelta(seconds=5)
    q, f = quote(now), features(now)
    result = engine.tick(q, f, now)
    evaluation.observe(q, f, result, now, config, engine.rules)
    assert audit_forward(path)["passed"]
    if action == "update":
        with closing(sqlite3.connect(path)) as db:
            assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    # A final normal connection lets SQLite safely clean its own WAL sidecars.
    with closing(sqlite3.connect(path)) as db:
        db.execute("SELECT COUNT(*) FROM forward_observations").fetchone()
    if action == "prepare":
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()
    print(json.dumps({"synthetic_test": True, "action": action, "passed": True}))
elif action == "expect-wal-failure":
    assert not Path(str(path) + "-wal").exists()
    assert not Path(str(path) + "-shm").exists()
    try:
        paper_status(path)
    except sqlite3.OperationalError as exc:
        assert exc.sqlite_errorcode & 255 in {sqlite3.SQLITE_CANTOPEN, sqlite3.SQLITE_READONLY}
        print(json.dumps({"reproduced_owner_failure": True, "sqlite_error_name": exc.sqlite_errorname,
                          "synthetic_test": True, "counts_as_week_evidence": False}))
    else:
        raise AssertionError("The read-only WAL case did not reproduce")
elif action == "verify":
    assert paper_status(path)["execution_mode"] == "paper"
    summary = forward_summary(path)
    assert summary["started_at"] == START.isoformat()
    assert audit_forward(path)["baseline_books_reconciled"] == 3
    manifest = json.loads((root / "cloud-manifest.json").read_text())
    assert manifest["fingerprint"] == cloud.fingerprint()
    with closing(sqlite3.connect(path)) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        try:
            db.execute("UPDATE balances SET cash_usd=cash_usd")
        except sqlite3.OperationalError as exc:
            assert exc.sqlite_errorcode & 255 == sqlite3.SQLITE_READONLY
        else:
            raise AssertionError("Read-only mount allowed an SQL write")
    print(json.dumps({"synthetic_test": True, "counts_as_week_evidence": False,
                      "readonly_database_read_passed": True, "writes_blocked": True,
                      "observations": summary["observations"], "account_identity_preserved": True}))
else:
    raise ValueError("Unknown synthetic check")
