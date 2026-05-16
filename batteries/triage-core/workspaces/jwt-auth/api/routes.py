"""API route handlers — depend directly on api.auth for token validation."""

from api.auth import require_auth

# Simulated user store
_USERS: dict[int, dict] = {
    1: {"id": 1, "name": "Alice", "role": "admin"},
    2: {"id": 2, "name": "Bob",   "role": "viewer"},
}


@require_auth
def get_current_user(payload: dict) -> dict:
    """Return the user record for the token owner."""
    uid = payload.get("user_id")
    user = _USERS.get(uid)
    if not user:
        raise LookupError(f"User {uid} not found")
    return user


@require_auth
def list_users(payload: dict) -> list[dict]:
    """Admin-only: list all users."""
    if payload.get("role") != "admin":
        raise PermissionError("Admin role required")
    return list(_USERS.values())


def login(user_id: int) -> str:
    """Return a fresh token for user_id (no password check — demo only)."""
    from api.auth import create_token
    user = _USERS.get(user_id)
    if not user:
        raise LookupError(f"Unknown user {user_id}")
    return create_token({"user_id": user_id, "role": user["role"]})
