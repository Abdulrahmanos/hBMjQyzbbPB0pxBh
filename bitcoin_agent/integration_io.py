"""Bounded network I/O and environment-only credentials. Never log HTTP bodies."""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class DeliveryError(ValueError):
    def __init__(self, code, *, ambiguous=False, permanent=False, retry_after=60):
        super().__init__(code)
        self.ambiguous, self.permanent, self.retry_after = ambiguous, permanent, retry_after


def http_json(url, *, body=None, headers=None, timeout=8, ambiguous_on_failure=False):
    """Only callers construct fixed provider endpoints; raw errors may contain tokens."""
    data = json.dumps(body, allow_nan=False).encode() if body is not None else None
    request = Request(url, data=data, headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})})
    try:
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(131073)
        if len(raw) > 131072:
            raise DeliveryError("response_too_large", ambiguous=ambiguous_on_failure)
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError("Expected object")
        return result
    except HTTPError as exc:
        retry = exc.headers.get("Retry-After", "60")
        retry = min(3600, max(1, int(retry))) if retry.isdigit() else 60
        raise DeliveryError(f"http_{exc.code}", ambiguous=ambiguous_on_failure and exc.code >= 500,
                            permanent=400 <= exc.code < 500 and exc.code != 429, retry_after=retry) from None
    except (URLError, TimeoutError, OSError):
        raise DeliveryError("network_failure", ambiguous=ambiguous_on_failure) from None
    except (ValueError, UnicodeError):
        raise DeliveryError("invalid_provider_response", ambiguous=ambiguous_on_failure) from None


def require_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise DeliveryError(f"missing_{name.lower()}", permanent=True)
    return value


def oauth_token(prefix):
    data = urlencode({"client_id": require_env(prefix + "_CLIENT_ID"),
                      "client_secret": require_env(prefix + "_CLIENT_SECRET"),
                      "refresh_token": require_env(prefix + "_REFRESH_TOKEN"), "grant_type": "refresh_token"}).encode()
    request = Request("https://oauth2.googleapis.com/token", data=data,
                      headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=8) as response:
            payload = json.loads(response.read(16384))
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("Missing token")
        return token
    except (HTTPError, URLError, OSError, ValueError, UnicodeError):
        raise DeliveryError("oauth_refresh_failed") from None


def load_env(path=".env"):
    """Small literal KEY=value loader; no interpolation, execution or secret output."""
    target = Path(path)
    if not target.is_file():
        return
    allowed = {"GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET", "GOOGLE_REFRESH_TOKEN", "GOOGLE_SHEET_ID",
               "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET",
               "GMAIL_REFRESH_TOKEN", "GMAIL_REPORT_RECIPIENT", "OPENAI_API_KEY", "LLM_MODEL"}
    for line in target.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, separator, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not separator or key not in allowed:
            raise ValueError("Unsupported .env entry; use the integration names in .env.example")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError("Invalid .env literal")
        os.environ.setdefault(key, value)


def provider_id(value, label):
    if not re.fullmatch(r"[A-Za-z0-9_-]{10,160}", value):
        raise ValueError(f"Invalid {label}")
    return value
