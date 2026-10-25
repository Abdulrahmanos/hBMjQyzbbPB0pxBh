"""Download only the pinned public Qwen file; verify all bytes before using it."""
import json
import sys
import uuid
from pathlib import Path
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bitcoin_agent.local_adviser import verify_model_file

def prepare(destination):
    lock = json.loads((ROOT / "config/open-model.json").read_text(encoding="utf-8"))
    target = Path(destination).resolve()
    if target.exists():
        verify_model_file(target, lock["sha256"], lock["bytes"])
        return {"status": "verified_existing", "sha256": lock["sha256"], "bytes": lock["bytes"]}
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + "." + uuid.uuid4().hex + ".part")
    url = "https://huggingface.co/" + lock["repository"] + "/resolve/" + lock["revision"] + "/" + lock["filename"]
    if not url.startswith("https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct-GGUF/resolve/"):
        raise ValueError("Unapproved model publisher")
    total = 0
    with urlopen(Request(url, headers={"User-Agent": "BitcoinPaperModelSetup/1"}), timeout=60) as source, temporary.open("xb") as output:
        while part := source.read(1024 * 1024):
            total += len(part)
            if total > lock["bytes"]:
                raise ValueError("Model download exceeded declared size")
            output.write(part)
    verify_model_file(temporary, lock["sha256"], lock["bytes"])
    temporary.replace(target)
    target.chmod(0o444)
    return {"status": "downloaded_and_verified", "sha256": lock["sha256"], "bytes": total}

if __name__ == "__main__":
    print(json.dumps(prepare(sys.argv[1] if len(sys.argv) > 1 else "/app/var/model.gguf")))
