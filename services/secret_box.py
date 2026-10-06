"""Login secrets at rest: Windows DPAPI in the current user's scope.

Only the same Windows account on the same machine can decrypt, so the
database file alone gives nothing away. Secrets never reach logs, API
responses or backups (see services/login_store.py).
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes

CRYPTPROTECT_UI_FORBIDDEN = 0x1
_ENTROPY = b"tthol-reader/login"  # binds the blobs to this app


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _blob(data: bytes) -> tuple[_Blob, ctypes.Array]:
    buf = ctypes.create_string_buffer(data, len(data))
    return _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf


def _take(out: _Blob) -> bytes:
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def protect(text: str) -> bytes:
    data, _keep = _blob(text.encode("utf-8"))
    entropy, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(data),
        None,
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    ):
        raise OSError("CryptProtectData failed")
    return _take(out)


def unprotect(blob: bytes) -> str:
    data, _keep = _blob(blob)
    entropy, _keep2 = _blob(_ENTROPY)
    out = _Blob()
    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(data),
        None,
        ctypes.byref(entropy),
        None,
        None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(out),
    ):
        raise OSError("CryptUnprotectData failed")
    return _take(out).decode("utf-8")
