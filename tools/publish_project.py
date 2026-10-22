"""Disabled publisher: any future upload needs renewed explicit owner authorization."""

import argparse
import base64
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.finalize_project import create_review

REPOSITORY = "Abdulrahmanos/qytbcf1pULIvwxUO"
OWNER, NAME = REPOSITORY.split("/")
REMOTE = "https://github.com/" + REPOSITORY + ".git"


def git(*args, check=True, input=None):
    environment = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GCM_INTERACTIVE": "Never"}
    result = subprocess.run(["git", *args], cwd=ROOT, input=input, capture_output=True,
                            text=True, encoding="utf-8", env=environment, timeout=180)
    if check and result.returncode:
        raise ValueError("Git command failed: " + " ".join(args[:2]) + ". Inspect local authentication or branch state; no force-push is used.")
    return result


def credential():
    result = git("-c", "credential.interactive=never", "credential", "fill",
                 input="protocol=https\nhost=github.com\nusername=" + OWNER + "\n\n")
    values = dict(line.split("=", 1) for line in result.stdout.splitlines() if "=" in line)
    token = values.get("password")
    if not token:
        raise ValueError("GitHub credentials are unavailable. Sign in through Git Credential Manager; never paste a token into chat.")
    return token


def api(token, path, payload=None, method=None):
    request = Request("https://api.github.com" + path,
                      data=json.dumps(payload).encode() if payload is not None else None,
                      method=method or ("POST" if payload is not None else "GET"),
                      headers={"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json", "Content-Type": "application/json",
                               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "BitcoinPaperProjectPublisher"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def audit_staged_files():
    paths = git("ls-files", "-z").stdout.split("\0")
    private_id = ""
    private = ROOT / "config/google-sheet.json"
    if private.is_file():
        private_id = json.loads(private.read_text(encoding="utf-8"))["spreadsheet_id"]
    patterns = [r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,})\b",
                r"\b\d{6,12}:[A-Za-z0-9_-]{30,}\b",
                r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----", r"\bsk-(?:proj-|svcacct-)?[A-Za-z0-9_-]{32,}"]
    for path in filter(None, paths):
        if path.startswith(("var/", ".venv/", "data/raw/", "data/processed/")) or path in {
                "config/google-sheet.json", "reports/milestone-4/sheet-readback.json"}:
            raise ValueError("A private runtime or Sheet file was staged: " + path)
        name = Path(path).name
        if (name.startswith(".env") and path != ".env.example") or name.startswith(("credentials", "service-account")):
            raise ValueError("A private credential file was staged: " + path)
        data = (ROOT / path).read_bytes()
        if len(data) > 90_000_000:
            raise ValueError("Large media belongs in a release/upload, not this source commit: " + path)
        if path.endswith((".png", ".jpg", ".jpeg", ".gif")):
            continue
        if re.fullmatch(r"reports/lstm-comparison/weights-seed-(17|29|43)\.npz", path):
            import numpy as np
            with np.load(ROOT / path, allow_pickle=False) as weights:
                if not weights.files or any(weights[key].dtype.kind not in "fiu" or weights[key].size > 100000 for key in weights.files):
                    raise ValueError("Unexpected nonnumeric or oversized model artifact: " + path)
            continue
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise ValueError("Unexpected binary source file: " + path) from None
        if (private_id and private_id in content) or any(re.search(pattern, content) for pattern in patterns):
            raise ValueError("A sensitive pattern was found in " + path + "; content was not printed.")


def publish(token):
    settings = json.loads((ROOT / "config/submission.json").read_text(encoding="utf-8"))
    if settings.get("uploads_authorized") is not True:
        raise ValueError("Uploads are disabled by the owner's latest instruction. Keep all work local.")
    if settings.get("repository") != REPOSITORY:
        raise ValueError("Publication destination differs from the owner's authorized repository.")
    verification = create_review(ROOT / "var/forward", ROOT / "reports/final", require_complete=True)
    text = (ROOT / "README.md").read_text(encoding="utf-8-sig")
    final_status = "**Project status:** the seven-day prospective paper evaluation passed its declared coverage, freeze and accounting checks. Read the [final evaluation](reports/final/review.md). Historical research, the revised forward policy and unverified optional integrations remain distinguished below. YouTube is not a completion requirement."
    text, count = re.subn(r"<!-- PROJECT_STATUS_START -->.*?<!-- PROJECT_STATUS_END -->",
                          "<!-- PROJECT_STATUS_START -->\n" + final_status + "\n<!-- PROJECT_STATUS_END -->", text, flags=re.S)
    if count != 1:
        raise ValueError("The README completion marker is missing; review its status before publication.")
    summary = json.loads((ROOT / "reports/final/evaluation.json").read_text(encoding="utf-8"))
    final_window = f"The prospective window ran from **{summary['started_at']}** to **{summary['window_ends_at']}**. All seven individual coverage checks passed. The original interrupted account and recovery rehearsal remain separate; see the [final comparison and audit](reports/final/review.md)."
    for marker, replacement in [("FORWARD_STATUS", final_window), ("COMPLETION_WORK", "- The final evaluation is complete. External publication/submission requires owner authorization; YouTube is not required.")]:
        text, count = re.subn("<!-- " + marker + "_START -->.*?<!-- " + marker + "_END -->",
                             "<!-- " + marker + "_START -->\n" + replacement + "\n<!-- " + marker + "_END -->", text, flags=re.S)
        if count != 1:
            raise ValueError("A README finalization marker is missing: " + marker)
    (ROOT / "README.md").write_text(text, encoding="utf-8")
    if git("rev-parse", "--show-toplevel").stdout.strip().replace("\\", "/").lower() != ROOT.as_posix().lower():
        raise ValueError("Unexpected local Git root.")
    remote = git("remote", "get-url", "origin", check=False)
    if remote.returncode:
        git("remote", "add", "origin", REMOTE)
    elif remote.stdout.strip() != REMOTE:
        raise ValueError("Existing Git remote is different; it was not changed.")
    if git("branch", "--show-current").stdout.strip() != "main":
        raise ValueError("Publication expects the prepared main branch; no branch was reset.")
    git("add", "--all")
    audit_staged_files()
    if git("diff", "--cached", "--quiet", check=False).returncode:
        git("commit", "-m", "Complete Bitcoin paper evaluation, methodology and walkthrough")
    try:
        repo = api(token, "/repos/" + REPOSITORY)
    except HTTPError as exc:
        if exc.code != 404:
            raise
        repo = api(token, "/user/repos", {"name": NAME, "private": False,
                   "description": "Reproducible Bitcoin DCA, swing and reserve research with an audited prospective paper evaluation", "auto_init": False})
    if repo.get("owner", {}).get("login", "").lower() != OWNER.lower() or repo.get("archived"):
        raise ValueError("The remote repository owner/state differs from the authorized destination.")
    git("push", "--set-upstream", "origin", "main")
    commit = git("rev-parse", "HEAD").stdout.strip()
    if git("ls-remote", "origin", "refs/heads/main").stdout.split()[0] != commit:
        raise ValueError("The remote branch does not match the local publication commit.")
    readme = api(token, "/repos/" + REPOSITORY + "/contents/README.md?ref=" + commit)
    if base64.b64decode(readme["content"]) != (ROOT / "README.md").read_bytes():
        raise ValueError("The remote README differs from the reviewed local version.")
    receipt = {"published_at": datetime.now(timezone.utc).isoformat(), "repository": "https://github.com/" + REPOSITORY,
               "commit": commit, "readme": "https://github.com/" + REPOSITORY + "/blob/main/README.md",
               "remote_commit_verified": True, "remote_readme_verified": True,
               "final_evaluation_complete": verification["final_evaluation_complete"]}
    output = ROOT / "var/publication/receipt.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--check-auth", action="store_true")
    modes.add_argument("--publish", action="store_true")
    args = parser.parse_args()
    try:
        if args.publish:
            settings = json.loads((ROOT / "config/submission.json").read_text(encoding="utf-8"))
            if settings.get("uploads_authorized") is not True:
                raise ValueError("Uploads are disabled by the owner's latest instruction. Keep all work local.")
        token = credential()
        user = api(token, "/user")
        if user.get("login", "").lower() != OWNER.lower():
            raise ValueError("The authenticated GitHub account differs from the authorized owner.")
        result = {"authenticated_owner": user["login"], "repository": REPOSITORY, "remote_writes": False} if args.check_auth else publish(token)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        # Never print credential-helper stdout, authorization headers or HTTP payloads.
        message = str(exc) if isinstance(exc, ValueError) else type(exc).__name__ + ": authentication, network or file operation failed; no secret was printed."
        print(message, file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
