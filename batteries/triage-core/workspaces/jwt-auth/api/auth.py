"""JWT token validation — currently inlined in the auth module."""

import hmac
import hashlib
import json
import os
import time
from base64 import urlsafe_b64decode, urlsafe_b64encode

_SECRET = os.getenv("JWT_SECRET", "dev-only-secret-change-in-prod").encode()


def _b64_encode(data: bytes) -> str:
    return urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64_decode(data: str) -> bytes:
    padding = 4 - len(data) % 4
    return urlsafe_b64decode(data + "=" * padding)


def _sign(header_b64: str, payload_b64: str) -> str:
    msg = f"{header_b64}.{payload_b64}".encode()
    return _b64_encode(hmac.new(_SECRET, msg, hashlib.sha256).digest())


def create_token(payload: dict, ttl_seconds: int = 3600) -> str:
    payload = {**payload, "exp": int(time.time()) + ttl_seconds}
    header = _b64_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = _b64_encode(json.dumps(payload).encode())
    sig = _sign(header, body)
    return f"{header}.{body}.{sig}"


def validate_token(token: str) -> dict | None:
    """Return the payload dict if the token is valid, else None."""
    try:
        header_b64, payload_b64, sig = token.split(".")
    except ValueError:
        return None
    if not hmac.compare_digest(sig, _sign(header_b64, payload_b64)):
        return None
    payload = json.loads(_b64_decode(payload_b64))
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def require_auth(func):
    """Decorator: injects validated payload as first positional arg."""
    def wrapper(token: str, *args, **kwargs):
        payload = validate_token(token)
        if payload is None:
            raise PermissionError("Invalid or expired token")
        return func(payload, *args, **kwargs)
    return wrapper
