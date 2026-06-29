"""Team management helpers for Sprint 37.2."""

from __future__ import annotations

import re
from typing import Any

from dealer_intelligence import format_activity_timestamp
from permissions import (
    USER_ROLE_ADMIN,
    USER_ROLES,
    USER_STATUS_ACTIVE,
    USER_STATUS_DISABLED,
    role_label,
    status_label,
)

Record = dict[str, Any]

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def normalize_email(email: str) -> str:
    return email.strip().lower()


def validate_new_user(name: str, email: str, role: str) -> str | None:
    cleaned_name = name.strip()
    cleaned_email = normalize_email(email)
    cleaned_role = role.strip().lower()

    if not cleaned_name:
        return "Name is required."
    if not cleaned_email or not EMAIL_PATTERN.fullmatch(cleaned_email):
        return "A valid email address is required."
    if cleaned_role not in USER_ROLES:
        return "Role must be Admin, Trader, or Viewer."
    return None


def validate_user_update(name: str, role: str) -> str | None:
    cleaned_name = name.strip()
    cleaned_role = role.strip().lower()

    if not cleaned_name:
        return "Name is required."
    if cleaned_role not in USER_ROLES:
        return "Role must be Admin, Trader, or Viewer."
    return None


def build_team_user_row(user: Record) -> Record:
    status = str(user.get("status") or USER_STATUS_ACTIVE).lower()
    last_login = user.get("last_login_at")
    return {
        "id": user["id"],
        "name": user.get("name") or "N/A",
        "email": user.get("email") or "N/A",
        "role": user.get("role") or USER_ROLE_ADMIN,
        "role_label": role_label(user.get("role")),
        "status": status,
        "status_label": status_label(status),
        "status_class": "success" if status == USER_STATUS_ACTIVE else "secondary",
        "created_at": format_activity_timestamp(user.get("created_at")),
        "last_login": format_activity_timestamp(last_login) if last_login else "—",
        "can_disable": status == USER_STATUS_ACTIVE,
        "can_enable": status == USER_STATUS_DISABLED,
    }


def build_team_user_rows(users: list[Record]) -> list[Record]:
    return [build_team_user_row(user) for user in users]
