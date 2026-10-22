"""Single-writer worker, restart supervisor, consistent backups and read-only health."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sqlite3
import subprocess
import sys
import sysconfig
import time
import uuid
from contextlib import closing
from datetime import datetime, timedelta, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from threading import Event

from .config import load_config
from .forward import ForwardEvaluation, audit_forward, write_forward_report
from .integrations import load_integration_settings
from .paper_store import connection, event, initialize_paper
from .paper_types import utc
from .remote_config import atomic_json


def now_utc():
    return datetime.now(timezone.utc)


def _execution_state(flags):
    import ctypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    function = kernel.SetThreadExecutionState
    function.argtypes, function.restype = [ctypes.c_uint], ctypes.c_uint
    return function(flags)


class AwakeRequest:
    """Hold a Windows idle-sleep request only while this supervisor is alive."""
    def __init__(self, enabled=None):
        self.enabled = os.name == "nt" if enabled is None else enabled
        self.previous = None

    def __enter__(self):
        if self.enabled:
            self.previous = _execution_state(0x80000001)  # continuous + system required; display may sleep
            if not self.previous:
                raise OSError("Windows did not accept the paper supervisor's awake request")
        return self

    def __exit__(self, *_):
        if self.enabled and self.previous:
            if not _execution_state(self.previous | 0x80000000):
                raise OSError("Windows did not restore the previous execution state")


class ProcessLock:
    """OS-held lock; process death releases it, with no stale-PID deletion logic."""
    def __init__(self, path):
        self.path = Path(path)
        self.stream = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.stream = self.path.open("a+b")
        self.stream.seek(0, 2)
        if self.stream.tell() == 0:
            self.stream.write(b"0")
            self.stream.flush()
        self.stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.stream.close()
            raise ValueError("Another process already owns this runtime lock") from None
        return self

    def __exit__(self, *_):
        if os.name == "nt":
            import msvcrt
            self.stream.seek(0)
            msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        self.stream.close()


def log_for(directory, name):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("bitcoin_runtime_" + name + "_" + str(directory.resolve()))
    if not logger.handlers:
        handler = RotatingFileHandler(directory / (name + ".jsonl"), maxBytes=5_000_000, backupCount=4, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def backup_database(path, destination):
    """SQLite online backup includes committed WAL; verify before publication."""
    source, target = Path(path).resolve(), Path(destination).resolve()
    if source == target or target.exists():
        raise ValueError("Backup must use a new destination outside the live database path")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original, closing(sqlite3.connect(temporary)) as snapshot:
        original.backup(snapshot, pages=256, sleep=.01)
        if snapshot.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ArithmeticError("Backup integrity check failed")
    audit = audit_forward(temporary)
    temporary.replace(target)
    metadata = {"created_at": now_utc().isoformat(), "path": str(target), "sha256": hashlib.sha256(target.read_bytes()).hexdigest(), "audit": audit}
    atomic_json(target.with_suffix(target.suffix + ".json"), metadata)
    return metadata


def runtime_health(directory, now=None):
    directory, now = Path(directory), utc(now or now_utc())
    supervisor, worker = read_json(directory / "supervisor.json"), read_json(directory / "worker.json")
    reasons = []
    def fresh(value, maximum):
        try:
            age = (now - utc(value)).total_seconds()
            return -5 <= age <= maximum
        except (TypeError, ValueError, AttributeError):
            return False
    if supervisor is None:
        reasons.append("supervisor_status_missing")
    elif not isinstance(supervisor, dict) or not fresh(supervisor.get("heartbeat_at"), 30):
        reasons.append("supervisor_heartbeat_stale")
    elif supervisor.get("state") != "running":
        reasons.append("supervisor_" + str(supervisor.get("state", "invalid")))
    if not isinstance(worker, dict):
        reasons.append("worker_status_missing")
    else:
        if not fresh(worker.get("heartbeat_at"), 90):
            reasons.append("worker_heartbeat_stale")
        if worker.get("state") != "running":
            reasons.append("worker_" + worker.get("state", "unknown"))
        last = worker.get("last_accepted_quote_at")
        if not fresh(last, 120):
            reasons.append("accepted_quote_stale_or_missing")
        if not worker.get("features_fresh"):
            reasons.append("features_unavailable_or_stale")
        if worker.get("observer_failed"):
            reasons.append("forward_observer_failure")
    return {"checked_at": now.isoformat(), "healthy": not reasons, "state": "healthy" if not reasons else "attention_required",
            "reasons": reasons, "supervisor": supervisor, "worker": worker}


def run_worker(config_path, db, directory, settings_path, *, cycles=0, supervisor_token=None):
    from .paper_service import run_paper
    directory = Path(directory).resolve()
    with ProcessLock(directory / "worker.lock"):
        config = load_config(config_path)
        settings = load_integration_settings(settings_path)
        if settings["delivery_mode"] != "demo" or settings["config_source"] != "none" or settings["adviser_provider"] != "none":
            raise ValueError("The initial forward run uses local delivery and a frozen policy, with no remote edits or LLM")
        initialize_paper(db, config)
        evaluation = ForwardEvaluation(db, config)
        audit_forward(db)
        logger = log_for(directory / "logs", "worker")
        last = read_json(directory / "worker.json") or {}
        state = {"pid": os.getpid(), "state": "starting", "heartbeat_at": now_utc().isoformat(),
                 "last_accepted_quote_at": last.get("last_accepted_quote_at"), "features_fresh": False,
                 "observer_failed": last.get("observer_failed", False), "config_checksum": config.checksum}
        atomic_json(directory / "worker.json", state)
        stopped = Event()
        def signal_stop(*_):
            stopped.set()
        for name in ("SIGTERM", "SIGINT"):
            signal.signal(getattr(signal, name), signal_stop)
        def should_stop():
            requested = stopped.is_set() or (directory / "STOP").exists() or (directory / "worker-stop").exists()
            if supervisor_token is not None:
                owner = read_json(directory / "supervisor.json")
                requested = requested or owner is None or owner.get("token") != supervisor_token or (now_utc() - utc(owner["heartbeat_at"])).total_seconds() > 45
            return requested
        def progress(line):
            payload = json.loads(line)
            logger.info(json.dumps({"logged_at": now_utc().isoformat(), **payload}))
        def observe(quote, features, result, at, active_config, rules):
            if result["accepted"]:
                try:
                    evaluation.observe(quote, features, result, at, active_config, rules)
                except (OSError, ValueError, ArithmeticError, sqlite3.Error):
                    state["observer_failed"] = True
                    with connection(db, write=True) as database:
                        event(database, at, "forward_observer_failure", {"reason": "observation_not_recorded", "quote_id": quote.quote_id})
                    progress(json.dumps({"event": "forward_observer_failed", "reason": "observation_not_recorded"}))
                state["last_accepted_quote_at"] = at.isoformat()
            state["features_fresh"] = features is not None and features.is_fresh(at, active_config.data["operational"]["feature_stale_after_seconds"])
        def heartbeat():
            state.update(heartbeat_at=now_utc().isoformat(), state="running")
            atomic_json(directory / "worker.json", state)
        try:
            result = run_paper(db, config, cycles=cycles, history="data/raw/btc_usd_1h.csv", runtime=directory / "market",
                               progress=progress, integration_settings=settings, observer=observe,
                               stop_requested=should_stop, heartbeat=heartbeat)
            state["state"] = "stopped"
            atomic_json(directory / "last-run.json", result)
            return result
        except BaseException:
            state["state"] = "failed"
            raise
        finally:
            state["heartbeat_at"] = now_utc().isoformat()
            atomic_json(directory / "worker.json", state)


def stop_child(child, stop_path, *, grace=75):
    stop_path.touch()
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        # The exact Popen handle identifies only this supervisor's own child.
        child.terminate()
        try:
            child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=10)


def worker_python():
    """Avoid the Windows venv redirector, so Popen owns the actual worker PID."""
    executable, environment = sys.executable, os.environ.copy()
    if os.name == "nt" and sys.prefix != sys.base_prefix:
        executable = getattr(sys, "_base_executable", None)
        if not executable or not Path(executable).is_file():
            raise ValueError("Cannot locate the actual Python worker interpreter")
        libraries = list(dict.fromkeys([sysconfig.get_path("purelib"), sysconfig.get_path("platlib")]))
        environment["PYTHONPATH"] = os.pathsep.join(libraries + ([environment["PYTHONPATH"]] if environment.get("PYTHONPATH") else []))
    return executable, environment


def supervise(config_path, db, directory, settings_path):
    directory, db = Path(directory).resolve(), Path(db).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    with ProcessLock(directory / "supervisor.lock"), AwakeRequest():
        logger = log_for(directory / "logs", "supervisor")
        stop_file, worker_stop = directory / "STOP", directory / "worker-stop"
        # Supervisor lock prevents two supervisors; worker.lock independently
        # prevents an orphaned worker and a replacement from trading together.
        child = None
        shutdown = Event()
        for name in ("SIGTERM", "SIGINT"):
            signal.signal(getattr(signal, name), lambda *_: shutdown.set())
        state = {"pid": os.getpid(), "token": uuid.uuid4().hex, "child_pid": None, "state": "starting", "heartbeat_at": now_utc().isoformat(), "restarts": 0}
        atomic_json(directory / "supervisor.json", state)
        if (directory / "COMPLETE").exists():
            state.update(state="stopped", completion_reason="seven_day_window_already_closed")
            atomic_json(directory / "supervisor.json", state)
            return state
        backoff, next_start, last_report, last_backup = 5, 0.0, 0.0, 0.0
        last_started, deadline = 0.0, None
        try:
            while not shutdown.is_set() and not stop_file.exists():
                clock = time.monotonic()
                if deadline is not None and now_utc() >= deadline:
                    state["completion_reason"] = "seven_day_window_elapsed"
                    (directory / "COMPLETE").touch()
                    break
                if child is None and clock >= next_start:
                    if worker_stop.exists():
                        worker_stop.unlink()
                    executable, environment = worker_python()
                    command = [executable, "-u", "-m", "bitcoin_agent", "--config", str(Path(config_path).resolve()), "--db", str(db),
                               "paper-worker", "--runtime", str(directory), "--settings", str(Path(settings_path).resolve()), "--supervisor-token", state["token"]]
                    output = (directory / "logs" / ("child-" + now_utc().strftime("%Y%m%dT%H%M%S%f") + ".log"))
                    output.parent.mkdir(parents=True, exist_ok=True)
                    with output.open("ab") as stream:
                        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stream, stderr=subprocess.STDOUT,
                                                 env=environment,
                                                 creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
                    state.update(child_pid=child.pid, state="running")
                    last_started = clock
                    logger.info(json.dumps({"event": "child_started", "at": now_utc().isoformat(), "pid": child.pid}))
                if child is not None and child.poll() is not None:
                    logger.info(json.dumps({"event": "child_exited", "at": now_utc().isoformat(), "code": child.returncode}))
                    state.update(child_pid=None, state="backoff", restarts=state["restarts"] + 1)
                    next_start = clock + backoff
                    backoff = min(300, backoff * 2) if clock - last_started < 300 else 5
                    child = None
                if child is not None and clock - last_started > 180:
                    worker = read_json(directory / "worker.json")
                    if worker is None or (now_utc() - utc(worker["heartbeat_at"])).total_seconds() > 180:
                        logger.info(json.dumps({"event": "stalled_child_recovery", "at": now_utc().isoformat()}))
                        stop_child(child, worker_stop, grace=10)
                        # poll() sees its exit next loop, retaining bounded backoff.
                state["heartbeat_at"] = now_utc().isoformat()
                atomic_json(directory / "supervisor.json", state)
                if db.is_file() and clock - last_report >= 60:
                    try:
                        summary = write_forward_report(db, directory / "reports")
                        atomic_json(directory / "health.json", runtime_health(directory))
                        if summary["status"] == "seven_day_window_closed":
                            state["completion_reason"] = "seven_day_window_elapsed"
                            (directory / "COMPLETE").touch()
                            break
                        if summary.get("window_ends_at"):
                            deadline = utc(summary["window_ends_at"])
                        if summary["observations"] and (last_backup == 0 or clock - last_backup >= 3600):
                            name = now_utc().strftime("%Y%m%dT%H%M%S%f") + ".sqlite3"
                            backup_database(db, directory / "backups" / name)
                            last_backup = clock
                    except (OSError, ValueError, ArithmeticError, sqlite3.Error) as exc:
                        logger.info(json.dumps({"event": "report_or_backup_failed", "at": now_utc().isoformat(), "type": type(exc).__name__}))
                    last_report = clock
                shutdown.wait(2)
        finally:
            if child is not None and child.poll() is None:
                stop_child(child, worker_stop)
            state.update(state="stopped", child_pid=None, heartbeat_at=now_utc().isoformat())
            atomic_json(directory / "supervisor.json", state)
            if db.is_file():
                try:
                    write_forward_report(db, directory / "reports")
                except (ValueError, sqlite3.Error):
                    pass
        return state
