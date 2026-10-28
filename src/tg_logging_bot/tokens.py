"""Short-lived, signed links into the web backup viewer.

Stateless by design: the token carries the owner id and an expiry,
HMAC-signed with a secret kept in the database (see
``Storage.get_or_create_web_secret``). No session store, no login form —
a fresh link is one tap of /backup away, and an old one simply stops
working on its own.
"""

from __future__ import annotations

import base64
import hmac
import time
from hashlib import sha256


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def issue_token(owner_id: int, secret: str, ttl_seconds: int) -> str:
    expires_at = int(time.time()) + ttl_seconds
    payload = f"{owner_id}.{expires_at}".encode()
    signature = hmac.new(secret.encode(), payload, sha256).digest()
    return f"{_b64(payload)}.{_b64(signature)}"


def verify_token(token: str, secret: str) -> int | None:
    """Return the owner id a valid, unexpired token was issued for, or None."""
    try:
        payload_b64, sig_b64 = token.split(".", 1)
        payload = _unb64(payload_b64)
        signature = _unb64(sig_b64)
        owner_str, expires_str = payload.decode().split(".")
    except (ValueError, TypeError):
        return None

    expected = hmac.new(secret.encode(), payload, sha256).digest()
    if not hmac.compare_digest(signature, expected):
        return None
    if int(expires_str) < int(time.time()):
        return None
    return int(owner_str)
