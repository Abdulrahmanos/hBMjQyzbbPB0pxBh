"""Owner-run installer for a separate Gmail worker; never changes the paper service."""
from __future__ import annotations

import getpass
import json
import os
import re
import subprocess
from pathlib import Path

KEYS = ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN", "GMAIL_REPORT_RECIPIENT")
BASE = Path(__file__).resolve().parent


def credentials(text):
    values = json.loads(text)
    if not isinstance(values, dict) or set(values) != set(KEYS):
        raise ValueError("Expected exactly the four Gmail fields")
    if any(not isinstance(v, str) or not v or len(v) > 8192
           or any(c.isspace() or ord(c) < 32 for c in v) for v in values.values()):
        raise ValueError("Invalid credential format")
    if not re.fullmatch(r"[A-Za-z0-9._-]+\.apps\.googleusercontent\.com", values["GMAIL_CLIENT_ID"]):
        raise ValueError("Invalid Gmail client ID")
    if not re.fullmatch(r"[^<>@,;]+@[^<>@,;]+\.[^<>@,;]+", values["GMAIL_REPORT_RECIPIENT"]):
        raise ValueError("Invalid recipient")
    return values


def inspect_paper():
    raw = subprocess.check_output(["docker", "inspect", "bitcoin-evaluation-paper-1"], text=True)
    paper = json.loads(raw)[0]
    labels = paper["Config"]["Labels"]
    if labels.get("com.docker.compose.project") != "bitcoin-evaluation" or labels.get("com.docker.compose.service") != "paper":
        raise ValueError("Unexpected paper container")
    if not paper["State"]["Running"] or paper["State"].get("Health", {}).get("Status") != "healthy":
        raise ValueError("Paper collector must already be healthy")
    mounts = [m for m in paper["Mounts"] if m["Destination"] == "/app/var"]
    if len(mounts) != 1 or mounts[0].get("Type") != "volume":
        raise ValueError("Unexpected paper evidence mount")
    image_id = paper["Image"]
    if not re.fullmatch(r"sha256:[a-f0-9]{64}", image_id):
        raise ValueError("Invalid pinned image identity")
    return image_id, mounts[0]["Name"]


def compose_config(image_id, volume):
    return {"name": "bitcoin-gmail", "services": {"mailer": {
        "image": image_id, "pull_policy": "never", "restart": "unless-stopped",
        "init": True, "read_only": True, "user": "10001:10001",
        "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"],
        "tmpfs": ["/tmp"], "mem_limit": "256m",
        "env_file": [{"path": str(BASE / "private/gmail.env"), "format": "raw"}],
        "command": ["python", "tools/cloud_gmail.py", "run"],
        "volumes": ["paper_evidence:/app/evidence:ro", "mail_state:/app/var",
                    str(BASE / "cloud_gmail.py") + ":/app/tools/cloud_gmail.py:ro"],
        "logging": {"driver": "json-file", "options": {"max-size": "2m", "max-file": "2"}},
    }}, "volumes": {"paper_evidence": {"external": True, "name": volume}, "mail_state": {}}}


def main():
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Run with sudo python3 on the AWS server.")
    if not (BASE / "cloud_gmail.py").is_file():
        raise SystemExit("Place cloud_gmail.py beside this installer.")
    image_id, volume = inspect_paper()
    private = BASE / "private"
    private.mkdir(mode=0o700, exist_ok=True)
    private.chmod(0o700)
    secret = private / "gmail.env"
    if not secret.exists():
        if not os.isatty(0):
            raise SystemExit("Run interactively so credentials can be pasted into a hidden prompt.")
        values = credentials(getpass.getpass("Paste the private cloud-gmail.json contents here (hidden): "))
        with secret.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write("".join(k + "=" + values[k] + "\n" for k in KEYS))
        secret.chmod(0o600)
        del values
    composition = BASE / "compose.gmail.json"
    config = compose_config(image_id, volume)
    if composition.exists() and json.loads(composition.read_text()) != config:
        raise SystemExit("Existing Gmail deployment differs; preserve it for review.")
    composition.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
    command = ["docker", "compose", "-f", str(composition)]
    # The test has a persistent identity; rerunning the installer does not resend it.
    subprocess.run(command + ["run", "--rm", "--no-deps", "mailer",
                              "python", "tools/cloud_gmail.py", "test"], check=True)
    subprocess.run(command + ["up", "-d", "--no-build", "--no-deps", "mailer"], check=True)
    print("GMAIL_SERVER_READY: separate sender enabled; paper service unchanged.")
    print("First due report for the current run: Monday 2 November 2026, 09:00 Asia/Riyadh.")
    print("Google Testing refresh tokens expire after seven days; reauthorize before later weekly reports.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.CalledProcessError):
        raise SystemExit("Gmail setup did not complete. No raw credentials or provider response was printed.") from None
