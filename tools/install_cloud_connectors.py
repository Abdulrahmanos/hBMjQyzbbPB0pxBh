"""Install only separate Telegram/Sheets workers beside an unchanged paper run."""
import json
import os
import re
import subprocess
from pathlib import Path
from install_cloud_gmail import inspect_paper

BASE = Path(__file__).resolve().parent


def telegram_credentials(values):
    if set(values) != {"TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}:
        raise ValueError("Unexpected Telegram credential fields")
    if not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]{25,80}", values["TELEGRAM_BOT_TOKEN"]):
        raise ValueError("Invalid Telegram token")
    if not re.fullmatch(r"-?[0-9]+", values["TELEGRAM_CHAT_ID"]):
        raise ValueError("Invalid private chat ID")
    return values


def compose_config(image, volume, base=BASE):
    services = {}
    for service in ("telegram", "sheets"):
        spec = {"image": image, "pull_policy": "never", "restart": "unless-stopped", "init": True,
                "read_only": True, "user": "10001:10001", "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"], "tmpfs": ["/tmp"], "mem_limit": "192m",
                "command": ["python", "tools/cloud_connectors.py", service, "run"],
                "volumes": ["paper_evidence:/app/evidence:ro", service + "_state:/app/var",
                            str(base / "cloud_connectors.py") + ":/app/tools/cloud_connectors.py:ro",
                            str(base / "cloud_gmail.py") + ":/app/tools/cloud_gmail.py:ro"],
                "logging": {"driver": "json-file", "options": {"max-size": "2m", "max-file": "2"}}}
        if service == "telegram":
            spec["env_file"] = [{"path": str(base / "private/telegram.env"), "format": "raw"}]
        else:
            spec["volumes"].append(str(base / "private/sheets.json") + ":/run/secrets/sheets.json:ro")
        services[service] = spec
    return {"name": "bitcoin-connectors", "services": services,
            "volumes": {"paper_evidence": {"external": True, "name": volume}, "telegram_state": {}, "sheets_state": {}}}


def main():
    if os.name != "posix" or os.geteuid() != 0:
        raise SystemExit("Run this installer with sudo on AWS")
    for name in ("cloud_connectors.py", "cloud_gmail.py", "private/telegram.env", "private/sheets.json"):
        if not (BASE / name).is_file():
            raise ValueError("Missing deployment file")
    (BASE / "private").chmod(0o700)
    (BASE / "private/telegram.env").chmod(0o600)
    # The root-only host directory hides this file; bind-mount gives only the
    # non-root Sheets container read access, never the Telegram container.
    os.chown(BASE / "private/sheets.json", 10001, 10001)
    (BASE / "private/sheets.json").chmod(0o400)
    image, volume = inspect_paper()
    composition = BASE / "compose.connectors.json"
    config = compose_config(image, volume)
    if composition.exists() and json.loads(composition.read_text()) != config:
        raise ValueError("Existing connector deployment differs; preserve its state")
    composition.write_text(json.dumps(config, indent=2) + "\n")
    cmd = ["docker", "compose", "-f", str(composition)]
    # Real read must pass before enabling the persistent Sheet worker.
    subprocess.run(cmd + ["run", "--rm", "--no-deps", "sheets", "python", "tools/cloud_connectors.py", "sheets", "once"], check=True)
    # Replay original fills with their dates, explicitly labelled as catch-up.
    subprocess.run(cmd + ["run", "--rm", "--no-deps", "telegram", "python", "tools/cloud_connectors.py", "telegram", "once"], check=True)
    subprocess.run(cmd + ["up", "-d", "--no-build", "--no-deps"], check=True)
    print("CONNECTORS_READY: Telegram alerts and hourly Sheets staging; frozen paper service unchanged")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError):
        raise SystemExit("Connector setup failed; no credentials or raw provider response printed") from None
