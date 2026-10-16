"""Read one explicitly selected generic-password item without permission dialogs."""
from __future__ import annotations

import ctypes
import sys
import threading

_ACCOUNT = b"agent-storage-guard"
_LOCK = threading.Lock()  # Security's interaction flag is process-wide.


def _security():
    framework = ctypes.CDLL("/System/Library/Frameworks/Security.framework/Security")
    signatures = {
        "SecKeychainGetUserInteractionAllowed": [ctypes.POINTER(ctypes.c_ubyte)],
        "SecKeychainSetUserInteractionAllowed": [ctypes.c_ubyte],
        "SecKeychainFindGenericPassword": [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p],
        "SecKeychainItemFreeContent": [ctypes.c_void_p, ctypes.c_void_p],
    }
    for name, arguments in signatures.items():
        function = getattr(framework, name)
        function.argtypes, function.restype = arguments, ctypes.c_int32
    return framework


def _read(native, service: bytes) -> str:
    length, data = ctypes.c_uint32(), ctypes.c_void_p()
    try:
        status = native.SecKeychainFindGenericPassword(None, len(service), service,
            len(_ACCOUNT), _ACCOUNT, ctypes.byref(length), ctypes.byref(data), None)
        if status:
            raise RuntimeError("keychain_locked_or_denied")
        if not data.value or not 0 < length.value <= 16384:
            raise RuntimeError("keychain_invalid_content")
        try:
            return ctypes.string_at(data, length.value).decode("utf-8")
        except UnicodeDecodeError:
            raise RuntimeError("keychain_invalid_content") from None
    finally:
        if data.value and native.SecKeychainItemFreeContent(None, data):
            raise RuntimeError("keychain_unavailable")


def read_keychain(service: str) -> str:
    """Read only the named service and fixed account; never unlock or prompt."""
    if not isinstance(service, str) or not service or "\x00" in service:
        raise RuntimeError("keychain_invalid_service")
    try:
        encoded = service.encode("utf-8")
    except UnicodeEncodeError:
        raise RuntimeError("keychain_invalid_service") from None
    if len(encoded) > 1024:
        raise RuntimeError("keychain_invalid_service")
    if sys.platform != "darwin":
        raise RuntimeError("keychain_unavailable")
    try:
        native = _security()
    except (OSError, AttributeError):
        raise RuntimeError("keychain_unavailable") from None
    with _LOCK:
        previous = ctypes.c_ubyte()
        if native.SecKeychainGetUserInteractionAllowed(ctypes.byref(previous)):
            raise RuntimeError("keychain_unavailable")
        try:
            if native.SecKeychainSetUserInteractionAllowed(0):
                raise RuntimeError("keychain_unavailable")
            return _read(native, encoded)
        finally:
            if native.SecKeychainSetUserInteractionAllowed(previous.value):
                raise RuntimeError("keychain_unavailable")
