"""Owner-operated Gmail OAuth setup. Secrets stay in ignored local files."""
import argparse
import base64
import hashlib
import hmac
import json
import secrets
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--client", default="var/secrets/gmail-client.json", help="Downloaded Google Desktop OAuth client JSON")
    args = parser.parse_args()
    path = ROOT / args.client
    if not path.is_file():
        parser.error("Download your Desktop OAuth client JSON to var/secrets/gmail-client.json first. See docs/notification-setup.md.")
    client = json.loads(path.read_text()).get("installed")
    if not isinstance(client, dict) or not client.get("client_id") or not client.get("client_secret"):
        parser.error("A Desktop (installed application) OAuth client is required")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    received = {}

    class Callback(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass  # Never log a callback URL containing an authorization code.

        def do_GET(self):
            query = parse_qs(urlparse(self.path).query)
            valid = urlparse(self.path).path == "/oauth2callback" and hmac.compare_digest(query.get("state", [""])[0], state)
            if valid and query.get("code"):
                received["code"] = query["code"][0]
            self.send_response(200 if valid else 400)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"Authorization received. You can close this tab." if "code" in received else b"Authorization was not completed.")

    with HTTPServer(("127.0.0.1", 0), Callback) as server:
        server.timeout = 2
        redirect = f"http://127.0.0.1:{server.server_port}/oauth2callback"
        # Fixed official endpoints: do not trust arbitrary endpoints in imported JSON.
        query = {"client_id": client["client_id"], "redirect_uri": redirect, "response_type": "code",
                 "scope": "https://www.googleapis.com/auth/gmail.send", "access_type": "offline", "prompt": "consent",
                 "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
        print("Complete Google's sign-in and consent yourself in the browser. Waiting up to five minutes; no secret will be printed.", flush=True)
        webbrowser.open("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(query))
        deadline = time.monotonic() + 300
        while "code" not in received and time.monotonic() < deadline:
            server.handle_request()
    if "code" not in received:
        raise SystemExit("Authorization timed out; no credentials changed.")
    body = urlencode({"client_id": client["client_id"], "client_secret": client["client_secret"],
                      "code": received["code"], "code_verifier": verifier, "redirect_uri": redirect,
                      "grant_type": "authorization_code"}).encode()
    request = Request("https://oauth2.googleapis.com/token", data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=20) as response:
            tokens = json.loads(response.read())
    except Exception:
        raise SystemExit("Google token exchange failed. No response or secret was printed.") from None
    if not tokens.get("refresh_token"):
        raise SystemExit("No offline refresh token returned; no credentials changed.")
    values = {"GMAIL_CLIENT_ID": client["client_id"], "GMAIL_CLIENT_SECRET": client["client_secret"], "GMAIL_REFRESH_TOKEN": tokens["refresh_token"]}
    env = ROOT / ".env"
    original = env.read_text() if env.exists() else ""
    lines = [line for line in original.splitlines() if line.partition("=")[0].strip() not in values]
    lines.extend(name + "=" + value for name, value in values.items())
    env.write_text("\n".join(lines) + "\n")
    print("Gmail OAuth saved to ignored .env. Set GMAIL_REPORT_RECIPIENT to your own email, then run tools/check_notifications.py.")


if __name__ == "__main__":
    main()
