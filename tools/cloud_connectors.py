"""Isolated AWS Telegram alerts and read-only Google Sheets staging.

Nothing in this worker can write to the frozen paper account. Sheet controls are
validated and cached for review; applying them requires a separately declared run.
"""
from __future__ import annotations
import argparse
import base64
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bitcoin_agent.config import load_config
from bitcoin_agent.integration_io import http_json, provider_id
from bitcoin_agent.notifications import TelegramSender, dispatch, enqueue, initialize_outbox, outbox_status
from bitcoin_agent.paper_store import connection
from bitcoin_agent.paper_types import utc
from bitcoin_agent.remote_config import ConfigManager, atomic_json
from bitcoin_agent.runtime import ProcessLock
from tools.cloud_gmail import bind

SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
TOKEN_URL = "https://oauth2.googleapis.com/token"
INTERVAL = 3600


def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def service_account(values):
    if values.get("type") != "service_account":
        raise ValueError("Expected service account credential")
    if not re.fullmatch(r"[a-z0-9-]+@[a-z0-9-]+\.iam\.gserviceaccount\.com", values.get("client_email", "")):
        raise ValueError("Invalid service identity")
    if values.get("token_uri") != TOKEN_URL:
        raise ValueError("Only official Google token endpoint is allowed")
    key = values.get("private_key", "")
    if not key.startswith("-----BEGIN " "PRIVATE KEY-----\n") or len(key) > 16384:
        raise ValueError("Invalid private key format")
    if not re.fullmatch(r"[a-f0-9]{40}", values.get("private_key_id", "")):
        raise ValueError("Invalid key identity")
    return values


class ServiceAccountRows:
    """Google's RS256 service-account flow; OpenSSL signs, no custom crypto.

    Fixed token/API hosts and read-only scope. Private keys never enter argv or
    logs. The temporary key is mode 0600 on the container's /tmp memory mount.
    """
    def __init__(self, credential_file):
        self.path = Path(credential_file)

    def fetch(self):
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        identity = provider_id(raw["sheet_id"], "Sheet ID")
        cred = service_account(raw["service_account"])
        now = int(datetime.now(timezone.utc).timestamp())
        header = {"alg": "RS256", "typ": "JWT", "kid": cred["private_key_id"]}
        claims = {"iss": cred["client_email"], "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + 600}
        signing = (b64(json.dumps(header, separators=(",", ":")).encode()) + "." +
                   b64(json.dumps(claims, separators=(",", ":")).encode())).encode()
        try:
            with tempfile.TemporaryDirectory(prefix="bitcoin-sheet-") as folder:
                key = Path(folder) / "key.pem"
                with key.open("x", encoding="ascii") as stream:
                    os.chmod(key, 0o600)
                    stream.write(cred["private_key"])
                signature = subprocess.run(["openssl", "dgst", "-sha256", "-sign", str(key)],
                                           input=signing, capture_output=True, timeout=10, check=True).stdout
        except (OSError, subprocess.SubprocessError):
            raise ValueError("Service account signing failed") from None
        assertion = signing.decode() + "." + b64(signature)
        body = urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion}).encode()
        try:
            request = Request(TOKEN_URL, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
            with urlopen(request, timeout=10) as response:
                payload = response.read(16385)
            if len(payload) > 16384:
                raise ValueError("Oversized token response")
            token = json.loads(payload)
            if not isinstance(token, dict):
                raise ValueError("Invalid token response")
        except (HTTPError, URLError, OSError, ValueError):
            raise ValueError("Service account token exchange failed") from None
        access = token.get("access_token")
        if not isinstance(access, str) or not access or len(access) > 16384:
            raise ValueError("Google access token unavailable")
        cells = quote("'Settings'!A5:C64", safe="")
        result = http_json(f"https://sheets.googleapis.com/v4/spreadsheets/{identity}/values/{cells}?valueRenderOption=UNFORMATTED_VALUE",
                           headers={"Authorization": "Bearer " + access})
        return result.get("values", [])


def telegram_cycle(runtime, state, now, sender=None):
    runtime, state, now = Path(runtime), Path(state), utc(now)
    run_id = bind(runtime, state)
    activation = state / "activation.json"
    if not activation.exists():
        atomic_json(activation, {"activated_at": now.isoformat(), "run_id": run_id})
    started = utc(json.loads(activation.read_text())["activated_at"])
    outbox = state / "outbox.sqlite3"
    initialize_outbox(outbox)
    # Read committed immutable fills. Only this worker's outbox is writable.
    with connection(runtime / "ledger.sqlite3") as source:
        fills = source.execute("SELECT f.*,o.reason FROM paper_fills f JOIN paper_orders o ON f.order_id=o.id ORDER BY f.occurred_at,f.id").fetchall()
    with connection(outbox, write=True) as target:
        for fill in fills:
            backfill = utc(fill["occurred_at"]) < started
            title = "PAPER trade catch-up (before alerts enabled)" if backfill else "PAPER trade confirmed"
            text = (f"{title}: {fill['side'].upper()} {fill['quantity']} BTC\n"
                    f"Sleeve: {fill['sleeve']} | Reason: {fill['reason']}\n"
                    f"Price: ${fill['price']} | Notional: ${fill['notional_usd']} | Fee: ${fill['fee_usd']}\n"
                    f"Trade time (UTC): {fill['occurred_at']}\nFill ID: {fill['id']}\n"
                    "Simulated execution; no real-money order.")
            enqueue(target, f"fill:{run_id}:{fill['id']}", "telegram", now,
                    {"text": text, "kind": "paper_fill", "backfill": backfill})
    delivered = dispatch(outbox, {"telegram": sender or TelegramSender()}, now, limit=5)
    rows = outbox_status(outbox)
    result = {"checked_at": now.isoformat(), "paper_access": "read_only", "fills_seen": len(fills),
              "delivery_results": delivered, "outbox": rows,
              "needs_attention": any(row["status"] in {"blocked", "uncertain"} for row in rows)}
    atomic_json(state / "status.json", result)
    return result


def sheets_cycle(runtime, state, now, source, *, baseline=None):
    state, now = Path(state), utc(now)
    bind(runtime, state)
    baseline = baseline or load_config(ROOT / "config/cloud-paper.json")
    status_file = state / "status.json"
    previous = json.loads(status_file.read_text()) if status_file.exists() else {}
    # Persist attempt cadence, including failures, so restarts do not hammer APIs.
    if previous.get("next_refresh_at") and now < utc(previous["next_refresh_at"]):
        return previous
    manager = ConfigManager(baseline, state / "validated-config.json", source)
    result = {"checked_at": now.isoformat(), "next_refresh_at": (now + timedelta(seconds=INTERVAL)).isoformat(),
              "mode": "staged_during_frozen_experiment", "applied_to_trades": False, "paper_access": "read_only"}
    atomic_json(status_file, {**result, "status": "refresh_in_progress", "needs_attention": True})
    refreshed = manager.refresh(now)
    result.update(refreshed)
    result["last_success_at"] = manager.last_success.isoformat() if manager.last_success else None
    result["needs_attention"] = refreshed["status"] != "source_validated"
    atomic_json(status_file, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=["telegram", "sheets"])
    parser.add_argument("action", choices=["run", "once", "status"])
    parser.add_argument("--runtime", default="/app/evidence/cloud-week")
    parser.add_argument("--state", default="/app/var/connector")
    parser.add_argument("--credentials", default="/run/secrets/sheets.json")
    args = parser.parse_args()
    state = Path(args.state)
    if args.action == "status":
        print((state / "status.json").read_text()); return 0
    stop = Event()
    for name in ("SIGINT", "SIGTERM"):
        signal.signal(getattr(signal, name), lambda *_: stop.set())
    with ProcessLock(state / "worker.lock"):
        while not stop.is_set():
            try:
                now = datetime.now(timezone.utc)
                result = (telegram_cycle(args.runtime, state, now) if args.service == "telegram" else
                          sheets_cycle(args.runtime, state, now, ServiceAccountRows(args.credentials)))
            except Exception as exc:
                result = {"checked_at": datetime.now(timezone.utc).isoformat(), "needs_attention": True,
                          "status": "connector_error", "error_type": type(exc).__name__}
                atomic_json(state / "error.json", result)
            if args.action != "run":
                print(json.dumps(result, indent=2)); return int(result.get("needs_attention", False))
            if result.get("delivery_results") or result.get("needs_attention"):
                print(json.dumps({k: result[k] for k in ("checked_at", "status", "error_type", "needs_attention") if k in result}), flush=True)
            stop.wait(30)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
