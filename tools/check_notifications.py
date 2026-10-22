"""Check credential readiness; optional explicit self-tests use the real adapters."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bitcoin_agent.integration_io import load_env, require_env, DeliveryError
from bitcoin_agent.notifications import GmailSender, TelegramSender

REQUIRED = {"telegram": ["TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"],
            "gmail": ["GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN", "GMAIL_REPORT_RECIPIENT"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--send-test", choices=["telegram", "gmail", "both"], help="Actually send clearly labelled messages to configured personal destinations")
    args = parser.parse_args()
    load_env(ROOT / ".env")
    readiness = {}
    for channel, names in REQUIRED.items():
        missing = []
        for name in names:
            try:
                require_env(name)
            except DeliveryError:
                missing.append(name)
        readiness[channel] = {"credentials_present": not missing, "missing_names": missing, "real_delivery_verified": False}
    now = datetime.now(timezone.utc)
    if args.send_test:
        channels = list(REQUIRED) if args.send_test == "both" else [args.send_test]
        for channel in channels:
            if not readiness[channel]["credentials_present"]:
                continue
            text = ("Bitcoin project PAPER integration test. This verifies notification delivery only. "
                    "No real trade was placed. Scheduled weekly reports are Monday 09:00 Asia/Riyadh (06:00 UTC).")
            payload = {"subject": "Bitcoin PAPER project: delivery test", "text": text}
            sender = TelegramSender() if channel == "telegram" else GmailSender()
            try:
                receipt = sender.send("self-test:" + now.isoformat() + ":" + channel, channel, payload)
            except (DeliveryError, OSError, ValueError) as exc:
                readiness[channel]["failure"] = str(exc) if isinstance(exc, DeliveryError) else "delivery_failed"
            else:
                readiness[channel].update(real_delivery_verified=True, receipt_received=bool(receipt), receipt_id=str(receipt))
    result = {"checked_at": now.isoformat(), "external_test_requested": bool(args.send_test), "channels": readiness}
    target = ROOT / "var/integrations/notification-readiness.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    if args.send_test:
        return 0 if all(readiness[channel]["real_delivery_verified"] for channel in channels) else 3
    return 0 if all(row["credentials_present"] for row in readiness.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
