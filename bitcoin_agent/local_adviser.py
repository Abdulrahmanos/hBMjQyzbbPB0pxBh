"""CPU-only local model advice. Fixed internal endpoint; no external API or tools."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from .adviser import SCHEMA, SYSTEM_PROMPT, validate_advice
from .config import _reject_constant, _unique_object
from .integration_io import http_json

def verify_model_file(path, expected, expected_bytes):
    target = Path(path)
    if target.stat().st_size != expected_bytes:
        raise ValueError("Local model size differs from the frozen model")
    digest = hashlib.sha256()
    with target.open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    if digest.hexdigest() != expected:
        raise ValueError("Local model checksum differs from the frozen model")
    return digest.hexdigest()

class LocalLlamaAdviser:
    def __init__(self, settings):
        self.settings = settings
        self.model = settings["alias"]
        self.last_receipt = None

    def suggest(self, context):
        self.last_receipt = None
        raw = json.dumps(context, sort_keys=True, allow_nan=False)
        if len(raw.encode()) > 4096:
            raise ValueError("Local model context too large")
        schema = json.loads(json.dumps(SCHEMA))
        for field in ("reason", "limitation"):
            schema["properties"][field].update(minLength=1, maxLength=200)
        payload = {"model": self.model, "stream": False, "temperature": 0,
                   "seed": self.settings["seed"], "max_tokens": self.settings["maximum_output_tokens"],
                   "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                {"role": "user", "content": raw}],
                   "response_format": {"type": "json_schema", "json_schema":
                       {"name": "shadow_advice", "strict": True, "schema": schema}}}
        result = http_json("http://model:8080/v1/chat/completions", body=payload, timeout=60)
        if result.get("model") != self.model:
            raise ValueError("Local response model does not match the declared model")
        choices = result.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise ValueError("Local model choices rejected")
        choice = choices[0]
        message = choice.get("message")
        if (choice.get("finish_reason") != "stop" or not isinstance(message, dict)
                or message.get("tool_calls") or message.get("refusal")):
            raise ValueError("Local model incomplete or refused")
        content = message.get("content")
        if not isinstance(content, str) or len(content.encode()) > 4096:
            raise ValueError("Local model content rejected")
        advice = validate_advice(json.loads(content, object_pairs_hook=_unique_object, parse_constant=_reject_constant))
        response_id, usage = result.get("id"), result.get("usage")
        if isinstance(response_id, str) and re.fullmatch(r"chatcmpl[-_A-Za-z0-9]{1,180}", response_id) and isinstance(usage, dict):
            counts = [usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens")]
            if (all(type(n) is int and n > 0 for n in counts)
                    and counts[1] <= self.settings["maximum_output_tokens"]
                    and counts[0] + counts[1] == counts[2] <= self.settings["context_tokens"]):
                self.last_receipt = {"provider": "local_llama_cpp", "response_id": response_id,
                    "returned_model": self.model, "model_sha256": self.settings["sha256"],
                    "input_tokens": counts[0], "output_tokens": counts[1], "total_tokens": counts[2],
                    "response_sha256": hashlib.sha256(json.dumps(result, sort_keys=True).encode()).hexdigest()}
        return advice
