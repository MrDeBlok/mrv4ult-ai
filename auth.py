"""Simple session auth for the internal MRV4ULT AI dashboard."""

from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse

from permissions import is_active_user, is_admin

Record = dict[str, Any]

SESSION_USER_ID_KEY = "user_id"
LOGIN_PATH = "/login"
PUBLIC_PATH_PREFIXES = ("/login", "/static", "/webhook/", "/health")

# Backward-compatible re-exports for existing imports.
USER_ROLE_ADMIN = "admin"
USER_ROLE_TRADER = "trader"
USER_ROLE_VIEWER = "viewer"


def session_secret_key() -> str:
    return os.getenv("SESSION_SECRET", "dev-session-secret-change-me")


def is_public_path(path: str) -> bool:
    if path in {LOGIN_PATH, "/health"} or path.startswith("/static/"):
        return True
    return path.startswith("/webhook/")


def get_current_user(request: Request) -> Record | None:
    """Return the logged-in user record attached to the session, if any."""
    if "session" not in request.scope:
        return None

    user_id = request.session.get(SESSION_USER_ID_KEY)
    if not user_id:
        return None

    from database import get_user_by_id, users_table_supported

    if not users_table_supported():
        return None

    user = get_user_by_id(str(user_id))
    if user is None or not is_active_user(user):
        request.session.pop(SESSION_USER_ID_KEY, None)
    return user


def require_current_user(request: Request) -> Record:
    """Return the logged-in user or raise an HTTP redirect/401."""
    user = get_current_user(request)
    if user is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user


def login_user(request: Request, user: Record) -> None:
    if "session" not in request.scope:
        return
    request.session[SESSION_USER_ID_KEY] = user["id"]
    from database import record_user_login

    record_user_login(str(user["id"]))


def logout_user(request: Request) -> None:
    if "session" not in request.scope:
        return
    request.session.pop(SESSION_USER_ID_KEY, None)


def authenticate_email(email: str) -> Record | None:
    """Passwordless pilot login by known user email."""
    from database import get_user_by_email, users_table_supported

    if not users_table_supported():
        return None
    normalized = email.strip().lower()
    if not normalized:
        return None
    user = get_user_by_email(normalized)
    if user is None or not is_active_user(user):
        return None
    return user


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse(url=LOGIN_PATH, status_code=303)
