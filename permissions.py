"""Centralized role and permission checks for MRV4ULT AI."""

from __future__ import annotations

from typing import Any

Record = dict[str, Any]

USER_ROLE_ADMIN = "admin"
USER_ROLE_TRADER = "trader"
USER_ROLE_VIEWER = "viewer"
USER_ROLES = frozenset({USER_ROLE_ADMIN, USER_ROLE_TRADER, USER_ROLE_VIEWER})

USER_STATUS_ACTIVE = "active"
USER_STATUS_DISABLED = "disabled"
USER_STATUSES = frozenset({USER_STATUS_ACTIVE, USER_STATUS_DISABLED})

VIEWER_ALLOWED_GET_EXACT = frozenset({"/", "/dashboard"})
VIEWER_ALLOWED_GET_PREFIXES = (
    "/activity",
    "/market-requests",
    "/matches",
    "/watch/",
)
VIEWER_FORBIDDEN_GET_PREFIXES = (
    "/import",
    "/parser-review",
    "/knowledge",
    "/settings/team",
    "/users",
)
TEAM_MANAGEMENT_PREFIX = "/settings/team"
WRITE_EXEMPT_POST_PATHS = frozenset({"/logout"})


def normalize_role(role: str | None) -> str:
    normalized = (role or USER_ROLE_TRADER).strip().lower()
    if normalized not in USER_ROLES:
        return USER_ROLE_TRADER
    return normalized


def normalize_status(status: str | None) -> str:
    normalized = (status or USER_STATUS_ACTIVE).strip().lower()
    if normalized not in USER_STATUSES:
        return USER_STATUS_ACTIVE
    return normalized


def user_role(user: Record | None) -> str:
    if not user:
        return USER_ROLE_TRADER
    return normalize_role(str(user.get("role")))


def user_status(user: Record | None) -> str:
    if not user:
        return USER_STATUS_DISABLED
    return normalize_status(str(user.get("status")))


def is_active_user(user: Record | None) -> bool:
    return bool(user) and user_status(user) == USER_STATUS_ACTIVE


def is_admin(user: Record | None) -> bool:
    return is_active_user(user) and user_role(user) == USER_ROLE_ADMIN


def is_trader(user: Record | None) -> bool:
    return is_active_user(user) and user_role(user) == USER_ROLE_TRADER


def is_viewer(user: Record | None) -> bool:
    return is_active_user(user) and user_role(user) == USER_ROLE_VIEWER


def can_manage_team(user: Record | None) -> bool:
    return is_admin(user)


def can_quick_fix_notifications(user: Record | None) -> bool:
    return is_admin(user) or is_trader(user)


def can_write(user: Record | None, path: str, *, method: str = "POST") -> bool:
    if not is_active_user(user):
        return False
    if is_viewer(user):
        return method == "POST" and path in WRITE_EXEMPT_POST_PATHS
    return is_admin(user) or is_trader(user)


def can_view_page(user: Record | None, path: str) -> bool:
    if not is_active_user(user):
        return False
    if is_admin(user) or is_trader(user):
        return True
    if not is_viewer(user):
        return False
    if path in VIEWER_ALLOWED_GET_EXACT:
        return True
    if any(path.startswith(prefix) for prefix in VIEWER_FORBIDDEN_GET_PREFIXES):
        return False
    return any(path.startswith(prefix) for prefix in VIEWER_ALLOWED_GET_PREFIXES)


def role_label(role: str | None) -> str:
    labels = {
        USER_ROLE_ADMIN: "Admin",
        USER_ROLE_TRADER: "Trader",
        USER_ROLE_VIEWER: "Viewer",
    }
    return labels.get(normalize_role(role), "Trader")


def status_label(status: str | None) -> str:
    labels = {
        USER_STATUS_ACTIVE: "Active",
        USER_STATUS_DISABLED: "Disabled",
    }
    return labels.get(normalize_status(status), "Active")
