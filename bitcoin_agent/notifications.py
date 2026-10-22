"""Paper fill alerts, period-correct reports and a persistent delivery outbox."""

from __future__ import annotations

import base64
import hashlib
import html
import json
import re
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path

from .integration_io import DeliveryError, http_json, oauth_token, require_env
from .paper_store import connection
from .paper_types import ONE, ZERO, decimal as D, utc
from .remote_config import atomic_json


def initialize_outbox(path):
    with connection(path, write=True) as database:
        database.execute("CREATE TABLE IF NOT EXISTS notification_outbox (id TEXT PRIMARY KEY, channel TEXT NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL, status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL, claimed_at TEXT, delivered_at TEXT, last_error TEXT, provider_id TEXT)")
        database.execute("CREATE INDEX IF NOT EXISTS outbox_due ON notification_outbox(status,next_attempt_at)")


def enqueue(database, identity, channel, now, payload):
    database.execute("INSERT OR IGNORE INTO notification_outbox (id,channel,created_at,payload_json,status,next_attempt_at) VALUES (?,?,?,?,'pending',?)",
                     (identity, channel, now.isoformat(), json.dumps(payload, sort_keys=True), now.isoformat()))


def queue_fills(path, now):
    """Scan immutable fills, including a fill committed before a process crash."""
    with connection(path, write=True) as database:
        for fill in database.execute("SELECT f.*,o.reason FROM paper_fills f JOIN paper_orders o ON f.order_id=o.id ORDER BY f.occurred_at,f.id").fetchall():
            text = (f"PAPER trade confirmed: {fill['side'].upper()} {fill['quantity']} BTC\n"
                    f"Sleeve: {fill['sleeve']} | Reason: {fill['reason']}\n"
                    f"Fill price: ${fill['price']} | Notional: ${fill['notional_usd']} | Fee: ${fill['fee_usd']}\n"
                    f"Observed: {fill['occurred_at']}\nFill ID: {fill['id']}\nSimulated execution; no funded order.")
            enqueue(database, "fill:" + fill["id"], "telegram", now,
                    {"kind": "paper_fill", "subject": "Paper trade confirmation", "text": text, "fill": dict(fill)})


def report_boundary(now, reporting):
    """Latest weekly scheduled instant, UTC-normalized; safe across restarts."""
    now = utc(now)
    zone = timezone.utc if reporting["timezone"] == "UTC" else timezone(timedelta(hours=3), "Asia/Riyadh")
    local = now.astimezone(zone)
    day = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday").index(reporting["weekly_day"])
    hour, minute = map(int, reporting["weekly_time"].split(":"))
    candidate = (local - timedelta(days=(local.weekday() - day) % 7)).replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate > local:
        candidate -= timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def weekly_report(path, end, now=None):
    end = utc(end)
    now = utc(now or datetime.now(timezone.utc))
    start = end - timedelta(days=7)
    # All observations are filtered by period end; no current balances leak back
    # into a historical report. Missing observations remain visibly unavailable.
    with connection(path) as database:
        initial = D(database.execute("SELECT value FROM app_metadata WHERE key='initial_cash_usd'").fetchone()[0])
        first = database.execute("SELECT observed_at FROM paper_marks ORDER BY observed_at LIMIT 1").fetchone()
        opening = database.execute("SELECT * FROM paper_marks WHERE observed_at<=? ORDER BY observed_at DESC LIMIT 1", (start.isoformat(),)).fetchone()
        closing = database.execute("SELECT * FROM paper_marks WHERE observed_at<=? ORDER BY observed_at DESC LIMIT 1", (end.isoformat(),)).fetchone()
        marks = database.execute("SELECT * FROM paper_marks WHERE observed_at>? AND observed_at<=? ORDER BY observed_at,quote_id", (start.isoformat(), end.isoformat())).fetchall()
        fills = database.execute("SELECT * FROM paper_fills WHERE occurred_at>? AND occurred_at<=? ORDER BY occurred_at,id", (start.isoformat(), end.isoformat())).fetchall()
        incidents = {"rejected_quotes": 0, "market_feed_failures": 0, "feature_refresh_failures": 0,
                     "rejected_config_refreshes": 0, "integration_worker_failures": 0}
        kinds = {"paper_quote_rejected": "rejected_quotes", "paper_market_failure": "market_feed_failures",
                 "paper_feature_refresh_failed": "feature_refresh_failures", "paper_integration_failure": "integration_worker_failures"}
        for row in database.execute("SELECT event_type,payload_json FROM system_events WHERE occurred_at>? AND occurred_at<=?", (start.isoformat(), end.isoformat())):
            if row["event_type"] in kinds:
                incidents[kinds[row["event_type"]]] += 1
            elif row["event_type"] == "integration_config_refresh" and json.loads(row["payload_json"]).get("status") == "remote_rejected_using_last_valid":
                incidents["rejected_config_refreshes"] += 1
    if first is None:
        coverage = "No accepted market observations; marked results unavailable."
    elif utc(first[0]) > start:
        coverage = f"Partial period: first accepted observation {first[0]}. Opening value uses initial simulated cash."
    else:
        coverage = "Observed period. Metrics use accepted quotes; price gaps are not interpolated."
    opening_equity = D(opening["equity_usd"]) if opening else initial
    peak, drawdown = opening_equity, ZERO
    for mark in marks:
        value = D(mark["equity_usd"])
        peak = max(peak, value)
        drawdown = max(drawdown, ONE - value / peak)
    equity = D(closing["equity_usd"]) if closing else None
    closing_stale = closing is None or (end - utc(closing["observed_at"])).total_seconds() > 120
    opening_stale = opening is not None and (start - utc(opening["observed_at"])).total_seconds() > 120
    fees = sum((D(row["fee_usd"]) for row in fills), ZERO)
    realized = sum((D(row["realized_pnl_usd"]) for row in fills), ZERO)
    metrics = {"execution_mode": "paper", "period_start": start.isoformat(), "period_end": end.isoformat(),
               "generated_at": now.isoformat(), "coverage": coverage, "accepted_marks": len(marks),
               "opening_equity_usd": str(opening_equity), "opening_mark_at": opening["observed_at"] if opening else None,
               "opening_mark_stale": opening_stale, "closing_mark_at": closing["observed_at"] if closing else None,
               "closing_mark_stale": closing_stale, "closing_equity_usd": str(equity) if equity is not None else None,
               "net_return_fraction": str(equity / opening_equity - ONE) if equity is not None else None,
               "observed_max_drawdown_fraction": str(drawdown) if marks else None,
               "cash_usd": closing["cash_usd"] if closing else None, "btc_units": closing["btc_units"] if closing else None,
               "phase_at_last_mark": closing["phase"] if closing else None, "fills": len(fills),
               "fees_usd": str(fees), "realized_pnl_usd": str(realized), "recorded_incidents": incidents}
    percentage = lambda value: f"{D(value) * 100:.2f}%" if value is not None else "Unavailable"
    text = (f"Bitcoin PAPER weekly report\nPeriod: {start.isoformat()} to {end.isoformat()} (start exclusive, end inclusive)\n"
            f"{coverage}\n\nOpening equity: ${opening_equity:.2f}\n"
            f"Closing equity: {'$' + format(equity, '.2f') if equity is not None else 'Unavailable'}\n"
            f"Net period return: {percentage(metrics['net_return_fraction'])}\n"
            f"Observed period maximum drawdown: {percentage(metrics['observed_max_drawdown_fraction'])}\n"
            f"Cash: {metrics['cash_usd'] or 'Unavailable'} USD | BTC: {metrics['btc_units'] or 'Unavailable'}\n"
            f"Confirmed fills: {len(fills)} | Fees: ${fees:.2f} | Realized P&L: ${realized:.2f}\n"
            f"Last accepted mark: {metrics['closing_mark_at'] or 'Unavailable'}\n"
            f"Closing mark stale: {closing_stale} | Opening mark stale: {opening_stale}\n"
            f"Risk phase at last mark: {metrics['phase_at_last_mark'] or 'Unavailable'}\n\n"
            f"Recorded period incidents: {json.dumps(incidents, sort_keys=True)}\n\n"
            "Equity = cash + BTC valued at the accepted bid/ask midpoint. Returns include modeled fills and fees; "
            "unrealized liquidation costs are not deducted. Drawdown measures observed marks only. "
            "Sparse or stale quotes can miss losses between observations. This is a paper experiment, not a live brokerage report.")
    return {"kind": "weekly_report", "subject": "Bitcoin PAPER weekly report " + end.date().isoformat(), "text": text, "metrics": metrics}


def queue_weekly(path, config, now):
    end = report_boundary(now, config.data["reporting"])
    with connection(path) as database:
        first = database.execute("SELECT observed_at FROM paper_marks ORDER BY observed_at LIMIT 1").fetchone()
    if first is None or utc(first[0]) > end:
        return None
    identity = "weekly:" + end.isoformat()
    with connection(path) as database:
        existing = database.execute("SELECT 1 FROM notification_outbox WHERE id=?", (identity,)).fetchone()
    if not existing:
        payload = weekly_report(path, end, now)
        with connection(path, write=True) as database:
            enqueue(database, identity, "gmail", utc(now), payload)
    return identity


class DemoInbox:
    """Deterministic files emulate the two delivery channels; no external messages."""
    local = True

    def __init__(self, directory):
        self.directory = Path(directory)

    def send(self, identity, channel, payload):
        filename = hashlib.sha256(identity.encode()).hexdigest() + ".json"
        atomic_json(self.directory / filename, {"id": identity, "channel": channel, "delivery": "local_demo", **payload})
        return "local:" + filename


class TelegramSender:
    local = False

    def send(self, identity, channel, payload):
        token, chat = require_env("TELEGRAM_BOT_TOKEN"), require_env("TELEGRAM_CHAT_ID")
        if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token) or not re.fullmatch(r"-?\d+", chat):
            raise DeliveryError("invalid_telegram_credentials", permanent=True)
        result = http_json("https://api.telegram.org/bot" + token + "/sendMessage",
                           body={"chat_id": chat, "text": payload["text"][:4000]}, ambiguous_on_failure=True)
        if result.get("ok") is not True:
            raise DeliveryError("telegram_rejected", permanent=True)
        receipt = result.get("result")
        message = receipt.get("message_id") if isinstance(receipt, dict) else None
        if type(message) is not int:
            raise DeliveryError("telegram_missing_receipt", ambiguous=True)
        return str(message)


class GmailSender:
    local = False

    def send(self, identity, channel, payload):
        recipient = require_env("GMAIL_REPORT_RECIPIENT")
        if not re.fullmatch(r"[^\s<>@,;\r\n]+@[^\s<>@,;\r\n]+\.[^\s<>@,;\r\n]+", recipient):
            raise DeliveryError("invalid_report_recipient", permanent=True)
        message = EmailMessage(policy=SMTP)
        message["To"], message["Subject"] = recipient, payload["subject"]
        message["Message-ID"] = "<" + hashlib.sha256(identity.encode()).hexdigest() + "@bitcoin-paper.local>"
        message.set_content(payload["text"])
        raw = base64.urlsafe_b64encode(message.as_bytes()).decode()
        result = http_json("https://gmail.googleapis.com/gmail/v1/users/me/messages/send", body={"raw": raw},
                           headers={"Authorization": "Bearer " + oauth_token("GMAIL")}, ambiguous_on_failure=True)
        if not isinstance(result.get("id"), str) or not result["id"]:
            raise DeliveryError("gmail_missing_receipt", ambiguous=True)
        return result["id"]


def dispatch(path, senders, now, *, limit=20):
    """Claim in SQLite, release locks before I/O. Uncertain live sends need review."""
    now = utc(now)
    results = []
    for _ in range(limit):
        with connection(path, write=True) as database:
            # A sending record surviving longer than two minutes may have reached
            # its provider. Local deterministic files can safely be written again.
            abandoned = database.execute("SELECT id,channel FROM notification_outbox WHERE status='sending' AND claimed_at<?",
                                         ((now - timedelta(minutes=2)).isoformat(),)).fetchall()
            for row in abandoned:
                sender = senders.get(row["channel"])
                status = "pending" if sender is not None and sender.local else "uncertain"
                database.execute("UPDATE notification_outbox SET status=?,last_error='interrupted_delivery' WHERE id=?", (status, row["id"]))
            rows = database.execute("SELECT * FROM notification_outbox WHERE status='pending' AND next_attempt_at<=? ORDER BY created_at,id", (now.isoformat(),)).fetchall()
            row = next((item for item in rows if item["channel"] in senders), None)
            if row is None:
                break
            database.execute("UPDATE notification_outbox SET status='sending',claimed_at=?,attempts=attempts+1 WHERE id=?", (now.isoformat(), row["id"]))
        try:
            receipt = senders[row["channel"]].send(row["id"], row["channel"], json.loads(row["payload_json"]))
        except (DeliveryError, OSError, ValueError) as exc:
            uncertain = isinstance(exc, DeliveryError) and exc.ambiguous
            permanent = isinstance(exc, DeliveryError) and exc.permanent
            status = "uncertain" if uncertain else "blocked" if permanent else "pending"
            delay = max(getattr(exc, "retry_after", 60), min(3600, 30 * 2 ** min(row["attempts"], 7)))
            # Preserve only our redacted provider error codes.
            error = str(exc) if isinstance(exc, DeliveryError) else "local_delivery_failure"
            with connection(path, write=True) as database:
                database.execute("UPDATE notification_outbox SET status=?,next_attempt_at=?,last_error=? WHERE id=?",
                                 (status, (now + timedelta(seconds=delay)).isoformat(), error, row["id"]))
            results.append({"id": row["id"], "status": status, "error": error})
        else:
            with connection(path, write=True) as database:
                database.execute("UPDATE notification_outbox SET status='delivered',delivered_at=?,provider_id=?,last_error=NULL WHERE id=?",
                                 (now.isoformat(), receipt, row["id"]))
            results.append({"id": row["id"], "status": "delivered"})
    return results


def outbox_status(path):
    with connection(path) as database:
        return [dict(row) for row in database.execute("SELECT id,channel,status,attempts,next_attempt_at,delivered_at,last_error FROM notification_outbox ORDER BY created_at,id")]


def retry_message(path, identity, now):
    with connection(path, write=True) as database:
        row = database.execute("SELECT status FROM notification_outbox WHERE id=?", (identity,)).fetchone()
        if row is None or row[0] not in {"uncertain", "blocked"}:
            raise ValueError("Only blocked or uncertain messages can be retried")
        database.execute("UPDATE notification_outbox SET status='pending',next_attempt_at=?,last_error='owner_retry' WHERE id=?", (utc(now).isoformat(), identity))


def render_inbox(path, directory):
    """Static, escaped local preview; all figures come from persisted payloads."""
    target = Path(directory)
    target.mkdir(parents=True, exist_ok=True)
    cards = []
    with connection(path) as database:
        rows = database.execute("SELECT * FROM notification_outbox ORDER BY created_at DESC,id").fetchall()
    for row in rows:
        payload = json.loads(row["payload_json"])
        cards.append(f"<article><div class='meta'>{html.escape(row['channel'])} demo · {html.escape(row['status'])} · attempts {row['attempts']}</div>"
                     f"<h2>{html.escape(payload['subject'])}</h2><pre>{html.escape(payload['text'])}</pre>"
                     f"<small>{html.escape(row['id'])}</small></article>")
    document = ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<title>Bitcoin paper demo inbox</title><style>body{font:16px system-ui;margin:40px auto;padding:0 24px;max-width:980px;background:#f5f6f8;color:#202636}"
                "h1{font-size:30px}article{background:white;border:1px solid #dbe0e9;border-radius:12px;padding:24px;margin:24px 0}"
                "h2{font-size:20px}.meta,small{color:#5c677a}pre{font:14px/1.65 system-ui;white-space:pre-wrap;overflow-wrap:anywhere}</style>"
                "<h1>Your project demo inbox</h1><p>Local previews of Telegram trade alerts and Monday email reports. All trades are simulated. "
                "No email address, bot or password is needed for this demo.</p>" + "".join(cards) + "</html>")
    (target / "index.html").write_text(document, encoding="utf-8")
    return target / "index.html"
