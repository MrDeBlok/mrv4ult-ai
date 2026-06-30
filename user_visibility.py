"""Per-user visibility rules for private contacts and imports."""

from __future__ import annotations

from typing import Any

from permissions import is_admin
from contact_classification import (
    CONTACT_TYPE_CLIENT,
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_REMOVED,
    has_valid_parsed_offers,
    normalize_contact_type,
)
from import_status import normalize_import_status

Record = dict[str, Any]

PRIVATE_IMPORT_STATUSES = frozenset(
    {"noise", "request_intent", "no_watch_detected", "insufficient_evidence"}
)


def user_columns_supported() -> bool:
    from database import user_ownership_columns_supported

    return user_ownership_columns_supported()


def is_private_import(import_log: Record) -> bool:
    """Return True when an import should not be shared across the team."""
    status = normalize_import_status(import_log)
    if status in PRIVATE_IMPORT_STATUSES:
        return True
    return not has_valid_parsed_offers(int(import_log.get("watches_parsed") or 0))


def is_shared_import(import_log: Record) -> bool:
    return not is_private_import(import_log)


def can_view_import(user: Record | None, import_log: Record) -> bool:
    """Team-shared business imports are visible to everyone; private imports are owner-only."""
    if user is None:
        return False
    if is_admin(user):
        return True
    if is_shared_import(import_log):
        return True
    owner_id = import_log.get("imported_by_user_id")
    if not owner_id:
        return True
    return str(owner_id) == str(user.get("id"))


def filter_imports_for_user(import_logs: list[Record], user: Record | None) -> list[Record]:
    return [import_log for import_log in import_logs if can_view_import(user, import_log)]


def contact_owner_user_id(contact: Record) -> str | None:
    """Return the user who owns a private/removed contact."""
    for key in ("owner_user_id", "classified_by_user_id"):
        value = contact.get(key)
        if value:
            return str(value)
    return None


def is_private_contact(contact: Record) -> bool:
    """Return True when a contact should not be shared across the team."""
    contact_type = normalize_contact_type(str(contact.get("contact_type") or ""))
    return contact_type not in {CONTACT_TYPE_DEALER, CONTACT_TYPE_CLIENT}


def can_view_contact(user: Record | None, contact: Record) -> bool:
    """Dealers and clients are shared; removed contacts are private to the owner."""
    if user is None:
        return False
    if not is_private_contact(contact):
        return True
    if is_admin(user):
        return True
    owner_id = contact_owner_user_id(contact)
    if not owner_id:
        return False
    return owner_id == str(user.get("id"))


def filter_contacts_for_user(contacts: list[Record], user: Record | None) -> list[Record]:
    return [contact for contact in contacts if can_view_contact(user, contact)]


def filter_contacts_page_for_user(
    contacts: list[Record],
    user: Record | None,
    *,
    filter_key: str,
    search_query: str = "",
) -> list[Record]:
    """Apply per-user visibility before people-page tab/search filters."""
    from contact_classification import build_contact_rows, filter_contact_rows

    visible_contacts = filter_contacts_for_user(contacts, user)
    rows = build_contact_rows(visible_contacts)
    return filter_contact_rows(rows, filter_key=filter_key, search_query=search_query)
