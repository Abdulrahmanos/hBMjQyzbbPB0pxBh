"""Owner-operated private-chat discovery for a dedicated project Telegram bot."""
import getpass
import json
import re
import secrets
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bitcoin_agent.integration_io import DeliveryError, http_json


def matched_chat(update, nonce):
    message = update.get("message", {})
    chat, author = message.get("chat", {}), message.get("from", {})
    if chat.get("type") == "private" and chat.get("id") == author.get("id") and message.get("text") == "/start " + nonce:
        return chat["id"]
    return None


def main():
    token = getpass.getpass("Dedicated project bot token (hidden; do not paste into chat): ").strip()
    if not re.fullmatch(r"\d+:[A-Za-z0-9_-]+", token):
        raise SystemExit("Invalid token format; nothing saved.")
    base = "https://api.telegram.org/bot" + token + "/"
    try:
        info = http_json(base + "getMe")
        if not info.get("ok") or not info.get("result", {}).get("username"):
            raise ValueError("Bot lookup failed")
        nonce = secrets.token_hex(16)
        print("Open this link yourself and press Start in your private chat:", flush=True)
        print("https://t.me/" + info["result"]["username"] + "?start=" + nonce, flush=True)
        print("Waiting up to three minutes. A username or phone number alone cannot receive private bot alerts.", flush=True)
        offset, destination = 0, None
        deadline = time.monotonic() + 180
        while destination is None and time.monotonic() < deadline:
            updates = http_json(base + "getUpdates", body={"offset": offset, "timeout": 2, "allowed_updates": ["message"]})
            if not updates.get("ok"):
                raise ValueError("Bot updates unavailable")
            for update in updates.get("result", []):
                offset = max(offset, int(update.get("update_id", -1)) + 1)
                destination = matched_chat(update, nonce)
                if destination is not None:
                    break
            if destination is None:
                time.sleep(1)
    except (DeliveryError, OSError, ValueError):
        raise SystemExit("Telegram setup failed. Verify the dedicated token and that no other poller/webhook uses this bot; no secret was printed or saved.") from None
    if destination is None:
        raise SystemExit("Private-chat verification timed out; nothing saved.")
    env = ROOT / ".env"
    lines = env.read_text().splitlines() if env.exists() else []
    lines = [line for line in lines if line.partition("=")[0].strip() not in {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}]
    lines.extend(["TELEGRAM_BOT_TOKEN=" + token, "TELEGRAM_CHAT_ID=" + str(destination)])
    env.write_text("\n".join(lines) + "\n")
    print("Verified private chat saved to ignored .env. Run tools/check_notifications.py --send-test telegram for a real labelled delivery test.")


if __name__ == "__main__":
    main()
