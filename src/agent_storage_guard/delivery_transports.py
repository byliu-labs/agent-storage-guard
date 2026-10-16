"""Feishu/Lark acceptance transport with bounded, redacted failures."""
from __future__ import annotations

import base64
import hashlib
import hmac
import http.client
import json
import math
import socket
import subprocess
import sys
import time
from urllib import error, request
from urllib.parse import urlsplit


class DeliveryError(RuntimeError):
    def __init__(self, code: str, retryable: bool = False, retry_after: float = 0):
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.retry_after = max(0, min(3600, retry_after)) if math.isfinite(retry_after) else 0


def check(provider: str, settings: dict) -> None:
    """Validate locally without contacting a provider or requesting permissions."""
    if provider != "feishu" or not isinstance(settings, dict):
        raise DeliveryError("invalid_configuration")
    value = settings.get("webhook_url")
    if (not isinstance(value, str) or len(value) > 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in value)):
        raise DeliveryError("invalid_configuration")
    try:
        url = urlsplit(value)
        valid = (url.scheme == "https" and url.hostname in {"open.feishu.cn", "open.larksuite.com"}
                 and url.port in (None, 443) and not url.username and not url.password
                 and not url.query and not url.fragment
                 and url.path.startswith("/open-apis/bot/v2/hook/")
                 and len(url.path.removeprefix("/open-apis/bot/v2/hook/")) > 0
                 and not any(c.isspace() for c in value))
    except ValueError:
        valid = False
    secret = settings.get("signing_secret", "")
    if (not valid or not isinstance(secret, str) or len(secret) > 4096
            or any(0xD800 <= ord(c) <= 0xDFFF for c in secret)):
        raise DeliveryError("invalid_configuration")
    if set(settings) - {"webhook_url", "signing_secret"}:
        raise DeliveryError("invalid_configuration")


class _NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _retry_after(headers) -> float:
    try:
        value = float(headers.get("Retry-After", "0"))
        return max(0, min(3600, value)) if math.isfinite(value) else 0
    except (ValueError, TypeError):
        return 0


def _http_request(url: str, payload: dict, headers: dict) -> bytes:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, body, {"Content-Type": "application/json", **headers}, method="POST")
    try:
        with request.build_opener(_NoRedirect).open(req, timeout=5) as response:
            data = response.read(65537)
            if len(data) > 65536:
                raise DeliveryError("provider_rejected")
            if response.length is not None and response.length > 0:
                raise DeliveryError("network_failure", True)
            return data
    except error.HTTPError as exc:
        code = exc.code
        delay = _retry_after(exc.headers) if code == 429 else 0
        exc.close()
        if code == 429:
            raise DeliveryError("rate_limited", True, delay) from None
        if 300 <= code < 400:
            raise DeliveryError("redirect_rejected") from None
        if code >= 500:
            raise DeliveryError("provider_unavailable", True) from None
        raise DeliveryError("provider_rejected") from None
    except (TimeoutError, socket.timeout):
        raise DeliveryError("timeout", True) from None
    except (error.URLError, OSError, http.client.HTTPException):
        raise DeliveryError("network_failure", True) from None


_DEADLINE_SECONDS = 8
_ERROR_CODES = {"timeout", "network_failure", "rate_limited", "redirect_rejected",
                "provider_unavailable", "provider_rejected"}


def _request(url: str, payload: dict, headers: dict) -> bytes:
    """A killable worker bounds the whole request, including slow-drip responses."""
    body = json.dumps({"url": url, "payload": payload, "headers": headers}, ensure_ascii=False)
    try:
        result = subprocess.run([sys.executable, "-m", __name__], input=body,
                                capture_output=True, text=True, timeout=_DEADLINE_SECONDS)
    except subprocess.TimeoutExpired:
        raise DeliveryError("timeout", True) from None
    except OSError:
        raise DeliveryError("network_failure", True) from None
    if result.returncode or len(result.stdout) > 90000:
        raise DeliveryError("network_failure", True)
    try:
        outcome = json.loads(result.stdout)
        if not isinstance(outcome, dict):
            raise ValueError
        if "data" in outcome:
            data = base64.b64decode(outcome["data"], validate=True)
            if len(data) > 65536:
                raise ValueError
            return data
        code = outcome["code"]
        delay = outcome.get("retry_after", 0)
        if code not in _ERROR_CODES or type(delay) not in (float, int):
            raise ValueError
    except (ValueError, TypeError, KeyError, RecursionError):
        raise DeliveryError("network_failure", True) from None
    raise DeliveryError(code, code in {"timeout", "network_failure", "rate_limited", "provider_unavailable"}, delay)


def _worker() -> None:
    """Private pipe protocol: neither credentials nor raw failures reach diagnostics."""
    try:
        incoming = json.loads(sys.stdin.read(65537))
        data = _http_request(incoming["url"], incoming["payload"], incoming["headers"])
        outcome = {"data": base64.b64encode(data).decode("ascii")}
    except DeliveryError as exc:
        outcome = {"code": exc.code, "retry_after": exc.retry_after}
    except Exception:
        # Last process boundary: suppress unexpected exception details containing secrets.
        outcome = {"code": "network_failure"}
    sys.stdout.write(json.dumps(outcome))


def send(provider: str, settings: dict, text: str) -> None:
    """Return only after provider acceptance; this does not prove user receipt."""
    check(provider, settings)
    if (not isinstance(text, str) or not text
            or any(0xD800 <= ord(c) <= 0xDFFF for c in text)
            or len(text.encode("utf-8")) > 16000):
        raise DeliveryError("invalid_payload")
    payload = {"msg_type": "text", "content": {"text": text}}
    if settings.get("signing_secret"):
        timestamp = str(int(time.time()))
        key = (timestamp + "\n" + settings["signing_secret"]).encode("utf-8")
        payload.update(timestamp=timestamp, sign=base64.b64encode(hmac.new(key, b"", hashlib.sha256).digest()).decode("ascii"))
    raw = _request(settings["webhook_url"], payload, {})
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError):
        raise DeliveryError("provider_rejected") from None
    if not isinstance(result, dict):
        raise DeliveryError("provider_rejected")
    status = result.get("code", result.get("StatusCode"))
    if (type(status) is not int or status != 0
            or any(type(result[key]) is not int or result[key] != 0
                   for key in ("code", "StatusCode") if key in result)):
        raise DeliveryError("provider_rejected")


if __name__ == "__main__":
    _worker()
