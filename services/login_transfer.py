"""Move the 帳號派發 list to another computer: one file sealed with a passphrase
the user picks (scrypt -> AES-256-GCM). DPAPI blobs only open on the machine
and Windows account that made them, so the export carries the secrets in
plain inside the sealed payload, and the import seals them again with the
new machine's DPAPI.
"""

from __future__ import annotations

import base64
import json
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

FORMAT = "tthol-logins"
VERSION = 1
MIN_PASSPHRASE = 8
# scrypt cost: ~0.1 s and 16 MB per try on a desktop, so guessing a
# passphrase from a leaked file is slow.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**14, 8, 1


class TransferError(ValueError):
    """Bad file or wrong passphrase. English message; the API maps it to Chinese."""


def _key(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))


def seal(entries: list[dict], passphrase: str) -> bytes:
    """entries: [{character, username, server, password, protect, enabled, sort, settings}]."""
    if len(passphrase) < MIN_PASSPHRASE:
        raise TransferError("passphrase too short")
    salt, nonce = os.urandom(16), os.urandom(12)
    plain = json.dumps({"entries": entries}, ensure_ascii=False).encode("utf-8")
    header = {
        "format": FORMAT,
        "version": VERSION,
        "kdf": "scrypt",
        "n": SCRYPT_N,
        "r": SCRYPT_R,
        "p": SCRYPT_P,
    }
    aad = json.dumps(header, sort_keys=True).encode("utf-8")
    sealed = AESGCM(_key(passphrase, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)).encrypt(nonce, plain, aad)
    body = {
        **header,
        "salt": base64.b64encode(salt).decode(),
        "nonce": base64.b64encode(nonce).decode(),
        "data": base64.b64encode(sealed).decode(),
        "count": len(entries),
    }
    return json.dumps(body, ensure_ascii=False, indent=1).encode("utf-8")


def open_sealed(raw: bytes | str, passphrase: str) -> list[dict]:
    try:
        body = json.loads(raw)
        if (
            body.get("format") != FORMAT
            or body.get("version") != VERSION
            or body.get("kdf") != "scrypt"
        ):
            raise TransferError("not a login export file")
        n, r, p = int(body["n"]), int(body["r"]), int(body["p"])
        if not (2**10 <= n <= 2**20 and 1 <= r <= 32 and 1 <= p <= 16):
            raise TransferError("unsupported scrypt parameters")
        salt = base64.b64decode(body["salt"])
        nonce = base64.b64decode(body["nonce"])
        sealed = base64.b64decode(body["data"])
    except TransferError:
        raise
    except (ValueError, KeyError, TypeError) as e:
        raise TransferError("not a login export file") from e
    header = {"format": FORMAT, "version": VERSION, "kdf": "scrypt", "n": n, "r": r, "p": p}
    aad = json.dumps(header, sort_keys=True).encode("utf-8")
    try:
        plain = AESGCM(_key(passphrase, salt, n, r, p)).decrypt(nonce, sealed, aad)
    except InvalidTag as e:
        raise TransferError("wrong passphrase or damaged file") from e
    entries = json.loads(plain).get("entries")
    if not isinstance(entries, list):
        raise TransferError("not a login export file")
    return [e for e in entries if isinstance(e, dict)]
