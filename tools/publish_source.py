"""Publish authorized source with honest readiness status, never invent completion."""
import argparse
import base64
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from publish_project import ROOT, OWNER, REPOSITORY, REMOTE, api, audit_staged_files, credential, git


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--message", default="Add historical LSTM comparison, project report and notification setup")
    args = parser.parse_args()
    settings = json.loads((ROOT / "config/submission.json").read_text())
    if settings.get("uploads_authorized") is not True or "github_repository" not in settings.get("authorized_channels", []):
        raise ValueError("GitHub publication is not authorized")
    if settings.get("repository") != REPOSITORY:
        raise ValueError("Publication destination mismatch")
    token = credential()
    user = api(token, "/user")
    if user.get("login", "").lower() != OWNER.lower():
        raise ValueError("Authenticated account differs from authorized owner")
    if not args.publish:
        print(json.dumps({"owner": user["login"], "repository": REPOSITORY, "remote_writes": False}))
        return
    summary = json.loads((ROOT / "reports/lstm-comparison/summary.json").read_text())
    if not all(summary["checks"].values()):
        raise ValueError("Historical experiment checks failed")
    readiness = json.loads((ROOT / "reports/submission/requirements.json").read_text())
    if readiness.get("claims_all_live_requirements_verified") is not False:
        raise ValueError("Readiness must accurately disclose unverified external integrations")
    tests = json.loads((ROOT / "reports/submission/verification.json").read_text())
    if tests.get("all_tests_passed") is not True:
        raise ValueError("The recorded test suite did not pass")
    if git("remote", "get-url", "origin").stdout.strip() != REMOTE or git("branch", "--show-current").stdout.strip() != "main":
        raise ValueError("Local remote or branch changed; nothing reset")
    git("add", "--all")
    audit_staged_files()
    # The configured recipient is private account information, not a source example.
    env = ROOT / ".env"
    private_values = []
    if env.exists():
        for line in env.read_text().splitlines():
            key, separator, value = line.partition("=")
            if separator and key.strip() in {"GMAIL_REPORT_RECIPIENT", "TELEGRAM_CHAT_ID"} and value.strip():
                private_values.append(value.strip())
    for path in filter(None, git("ls-files", "-z").stdout.split("\0")):
        if Path(path).suffix in {".png", ".npz"}:
            continue
        text = (ROOT / path).read_text(encoding="utf-8-sig")
        if any(value in text for value in private_values):
            raise ValueError("A private delivery destination was found in staged source; no content printed")
    if git("diff", "--cached", "--quiet", check=False).returncode:
        git("commit", "-m", args.message)
    from urllib.error import HTTPError
    try:
        repo = api(token, "/repos/" + REPOSITORY)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        repo = api(token, "/user/repos", {"name": REPOSITORY.split("/")[1], "private": False, "auto_init": False,
                   "description": "Bitcoin DCA/ATR paper trading, historical LSTM comparison, audited logs and notification integrations"})
    if repo.get("owner", {}).get("login", "").lower() != OWNER.lower() or repo.get("archived"):
        raise ValueError("Remote repository owner/state mismatch")
    git("push", "--set-upstream", "origin", "main")
    commit = git("rev-parse", "HEAD").stdout.strip()
    if git("ls-remote", "origin", "refs/heads/main").stdout.split()[0] != commit:
        raise ValueError("Remote commit verification failed")
    remote_readme = api(token, "/repos/" + REPOSITORY + "/contents/README.md?ref=" + commit)
    if base64.b64decode(remote_readme["content"]) != (ROOT / "README.md").read_bytes():
        raise ValueError("Remote README verification failed")
    receipt = {"published_at": datetime.now(timezone.utc).isoformat(), "repository": "https://github.com/" + REPOSITORY,
               "commit": commit, "remote_commit_verified": True, "remote_readme_verified": True,
               "all_live_requirements_verified": False, "external_delivery_pending_owner_credentials": True}
    target = ROOT / "var/publication/receipt.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        # Never print HTTP errors, helper stdout, URLs carrying tokens or response bodies.
        print(str(exc) if isinstance(exc, ValueError) else type(exc).__name__ + ": publication operation failed; no credential printed.")
        raise SystemExit(3)
