

import base64
import hashlib
import hmac
import os

ALGO = "pbkdf2_sha256"

ITERATIONS = 260000
SALT_BYTES = 16

def _b64e(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")

def _b64d(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"))

def _derive(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)

def hash_password(password: str, iterations: int = ITERATIONS) -> str:

    salt = os.urandom(SALT_BYTES)
    dk = _derive(password, salt, iterations)
    return "%s$%d$%s$%s" % (ALGO, iterations, _b64e(salt), _b64e(dk))

def is_hashed(stored: str) -> bool:

    return isinstance(stored, str) and stored.startswith(ALGO + "$") and stored.count("$") == 3

def verify_password(password: str, stored: str) -> bool:

    if not stored or not isinstance(stored, str):
        return False
    if not is_hashed(stored):
        return hmac.compare_digest(str(password), stored)
    try:
        _algo, iters, salt_b64, hash_b64 = stored.split("$")
        salt = _b64d(salt_b64)
        expect = _b64d(hash_b64)
        dk = _derive(str(password), salt, int(iters))
    except Exception:

        return False
    return hmac.compare_digest(dk, expect)

def needs_upgrade(stored: str) -> bool:

    if not stored:
        return False
    if not is_hashed(stored):
        return True
    try:
        return int(stored.split("$")[1]) < ITERATIONS
    except Exception:
        return True
