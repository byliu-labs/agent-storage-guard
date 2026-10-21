"""Opt-in Messages automation, with private input and bounded process lifetime."""
from __future__ import annotations

import os
import re
import subprocess
import sys

from .delivery_transports import DeliveryError

_OSASCRIPT = "/usr/bin/osascript"
_TIMEOUT_SECONDS = 10
_RECIPIENT = re.compile(r"(?:\+?[0-9]{7,15}|[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.[A-Za-z]{2,63})\Z")


def check(settings: dict) -> None:
    """Offline validation only: never launch Messages or inspect permissions."""
    if (not isinstance(settings, dict) or settings.keys() != {"recipient"}
            or not isinstance(settings["recipient"], str) or len(settings["recipient"]) > 254
            or not _RECIPIENT.fullmatch(settings["recipient"])):
        raise DeliveryError("invalid_configuration")
    if sys.platform != "darwin" or not os.access(_OSASCRIPT, os.X_OK):
        raise DeliveryError("imessage_platform_unavailable")


_SCRIPT = r"""
on deliver(recipientHandle, alertText)
    try
        tell application "Messages"
            set candidates to (every account whose service type is iMessage)
            repeat with candidate in candidates
                if (enabled of candidate) and ((connection status of candidate) is connected) then
                    send alertText to participant recipientHandle of candidate
                    return "accepted"
                end if
            end repeat
        end tell
        return "service_unavailable"
    on error errorText number errorNumber
        if errorNumber is -1743 then return "permission_denied"
        return "failed"
    end try
end deliver
deliver(INPUT)
"""


def _literal(value: str) -> str:
    """AppleScript string data; never interpolate unquoted source or use argv."""
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('\r', '\\r').replace('\n', '\\n').replace('\t', '\\t') + '"'


def send(settings: dict, text: str) -> None:
    """Native acceptance only; account setup and Automation approval are owner gates."""
    check(settings)
    if (not isinstance(text, str) or not text or len(text) > 16000
            or any(0xD800 <= ord(c) <= 0xDFFF for c in text)
            or len(text.encode("utf-8")) > 16000):
        raise DeliveryError("invalid_payload")
    data = _literal(settings["recipient"]) + ", " + _literal(text)
    script = _SCRIPT.replace("deliver(INPUT)", "deliver(" + data + ")")
    try:
        result = subprocess.run([_OSASCRIPT, "-"], input=script,
                                capture_output=True, text=True, timeout=_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        raise DeliveryError("timeout", True) from None
    except OSError:
        raise DeliveryError("imessage_platform_unavailable") from None
    outcomes = {"permission_denied": "imessage_permission_denied",
                "service_unavailable": "imessage_service_unavailable", "failed": "imessage_failed"}
    outcome = result.stdout.strip()
    if result.returncode or outcome != "accepted":
        raise DeliveryError(outcomes.get(outcome, "imessage_failed"))
