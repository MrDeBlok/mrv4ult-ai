"""Contact classification for MRV4ULT AI people and dealers."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

Record = dict[str, Any]

CONTACT_TYPE_DEALER = "dealer"
CONTACT_TYPE_CLIENT = "client"
CONTACT_TYPE_REMOVED = "removed"

CONTACT_TYPES = (
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_CLIENT,
)

RESTORE_CONTACT_TYPES = (
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_CLIENT,
)

ALL_CONTACT_TYPES = (
    CONTACT_TYPE_DEALER,
    CONTACT_TYPE_CLIENT,
    CONTACT_TYPE_REMOVED,
)

LEGACY_CONTACT_TYPES = frozenset(
    {"private", "ignored", "unknown", "deleted"},
)

CONTACTS_FILTER_ACTIVE = "active"
CONTACTS_FILTER_ALL = "all"
CONTACTS_FILTER_DEALERS = "dealers"
CONTACTS_FILTER_CLIENTS = "clients"
CONTACTS_FILTER_REMOVED = "removed"

CONTACTS_FILTERS = (
    CONTACTS_FILTER_ACTIVE,
    CONTACTS_FILTER_ALL,
    CONTACTS_FILTER_DEALERS,
    CONTACTS_FILTER_CLIENTS,
    CONTACTS_FILTER_REMOVED,
)

CONTACTS_FILTER_LABELS: dict[str, str] = {
    CONTACTS_FILTER_ACTIVE: "Active",
    CONTACTS_FILTER_ALL: "All",
    CONTACTS_FILTER_DEALERS: "Dealers",
    CONTACTS_FILTER_CLIENTS: "Clients",
    CONTACTS_FILTER_REMOVED: "Removed",
}

DEFAULT_CONTACTS_FILTER = CONTACTS_FILTER_ACTIVE

CONTACT_TYPE_LABELS: dict[str, str] = {
    CONTACT_TYPE_DEALER: "Dealer",
    CONTACT_TYPE_CLIENT: "Client",
    CONTACT_TYPE_REMOVED: "Removed",
}

CONTACT_TYPE_CLASSES: dict[str, str] = {
    CONTACT_TYPE_DEALER: "success",
    CONTACT_TYPE_CLIENT: "info",
    CONTACT_TYPE_REMOVED: "dark",
}

IMPORT_PLACEHOLDER_WHATSAPP_ID = "import-placeholder"
REDACTED_SENDER_LABEL = "Private contact"


def normalize_contact_type(
    raw_type: str | None,
    *,
    has_offers: bool = False,
) -> str:
    """Map stored or legacy contact_type values to dealer/client/removed."""
    normalized = (raw_type or "").strip().lower()
    if normalized in ALL_CONTACT_TYPES:
        return normalized
    if normalized == "deleted":
        return CONTACT_TYPE_REMOVED
    if normalized in {"private", "ignored"}:
        return CONTACT_TYPE_REMOVED
    if normalized == "unknown":
        return CONTACT_TYPE_DEALER if has_offers else CONTACT_TYPE_REMOVED
    if normalized == CONTACT_TYPE_DEALER:
        return CONTACT_TYPE_DEALER
    if not normalized:
        return CONTACT_TYPE_DEALER if has_offers else CONTACT_TYPE_REMOVED
    return CONTACT_TYPE_REMOVED


def is_business_contact(contact_type: str | None) -> bool:
    """Return True only for contacts treated as dealers in business views."""
    return normalize_contact_type(contact_type) == CONTACT_TYPE_DEALER


def is_client_contact(contact_type: str | None) -> bool:
    """Return True for customer/prospect contacts."""
    return normalize_contact_type(contact_type) == CONTACT_TYPE_CLIENT


def is_removed_contact(contact_type: str | None) -> bool:
    """Return True when a contact has been removed from active views."""
    return normalize_contact_type(contact_type) == CONTACT_TYPE_REMOVED


def should_process_business_import(contact_type: str | None) -> bool:
    """Whether matching, notifications, and deal analysis should run."""
    return is_business_contact(contact_type)


def has_valid_parsed_offers(watches_parsed: int) -> bool:
    """Return True when a message produced at least one parsed watch offer."""
    return watches_parsed > 0


def should_redact_import_sender(import_log: Record) -> bool:
    """Hide sender identity for imports without valid watch offers."""
    return not has_valid_parsed_offers(int(import_log.get("watches_parsed") or 0))


def format_import_sender_label(import_log: Record) -> str:
    """Format a sender label for activity views, redacting non-offer senders when needed."""
    if should_redact_import_sender(import_log):
        return REDACTED_SENDER_LABEL

    alias = import_log.get("dealer_alias")
    if isinstance(alias, str) and alias.strip():
        return alias.strip()

    whatsapp = import_log.get("dealer_whatsapp")
    if isinstance(whatsapp, str) and whatsapp.strip():
        return whatsapp.strip()

    return "N/A"


def normalize_whatsapp_id(value: str | None) -> str:
    return (value or "").strip()


def build_dealer_lookup_by_whatsapp(dealers: list[Record]) -> dict[str, Record]:
    """Index dealers by whatsapp_id and phone_number for import-log lookups."""
    lookup: dict[str, Record] = {}
    for dealer in dealers:
        whatsapp_id = normalize_whatsapp_id(dealer.get("whatsapp_id"))
        if whatsapp_id:
            lookup[whatsapp_id] = dealer
        phone_number = normalize_whatsapp_id(dealer.get("phone_number"))
        if phone_number and phone_number not in lookup:
            lookup[phone_number] = dealer
    return lookup


def contact_type_for_import_log(import_log: Record, lookup: dict[str, Record]) -> str:
    """Resolve the normalized contact type for an import log."""
    whatsapp = normalize_whatsapp_id(import_log.get("dealer_whatsapp"))
    dealer = lookup.get(whatsapp)
    if dealer:
        has_offers = has_valid_parsed_offers(int(import_log.get("watches_parsed") or 0))
        return normalize_contact_type(
            str(dealer.get("contact_type") or ""),
            has_offers=has_offers,
        )
    return CONTACT_TYPE_REMOVED


def is_business_import_log(import_log: Record, lookup: dict[str, Record]) -> bool:
    """Return True when an import belongs to a dealer contact with valid offers."""
    if not has_valid_parsed_offers(int(import_log.get("watches_parsed") or 0)):
        return False
    return is_business_contact(contact_type_for_import_log(import_log, lookup))


def filter_business_import_logs(
    import_logs: list[Record],
    lookup: dict[str, Record],
) -> list[Record]:
    """Exclude non-dealer contacts from business activity views."""
    return [
        import_log
        for import_log in import_logs
        if is_business_import_log(import_log, lookup)
    ]


def build_contact_row(dealer: Record) -> Record:
    """Format one contact for the people management page."""
    contact_type = normalize_contact_type(str(dealer.get("contact_type") or ""))
    display_name = (dealer.get("display_name") or "").strip()
    phone_number = normalize_whatsapp_id(dealer.get("phone_number"))
    whatsapp_id = normalize_whatsapp_id(dealer.get("whatsapp_id"))
    name = display_name or phone_number or whatsapp_id or "Unknown contact"
    return {
        "id": dealer.get("id"),
        "name": name,
        "whatsapp_id": whatsapp_id or "N/A",
        "phone_number": phone_number or "N/A",
        "contact_type": contact_type,
        "contact_type_label": CONTACT_TYPE_LABELS.get(contact_type, contact_type.title()),
        "contact_type_class": CONTACT_TYPE_CLASSES.get(contact_type, "secondary"),
        "updated_at": dealer.get("updated_at"),
    }


def build_contact_rows(dealers: list[Record]) -> list[Record]:
    rows = [build_contact_row(dealer) for dealer in dealers if dealer.get("id")]
    rows.sort(
        key=lambda row: (
            row["contact_type"] != CONTACT_TYPE_DEALER,
            row["contact_type"] != CONTACT_TYPE_CLIENT,
            row["name"].lower(),
        )
    )
    return rows


def parse_contacts_filter(value: str | None) -> str:
    """Normalize the contacts page filter query parameter."""
    normalized = (value or DEFAULT_CONTACTS_FILTER).strip().lower()
    if normalized == "business":
        return CONTACTS_FILTER_ACTIVE
    if normalized in {"dealer", "deleted"}:
        legacy_map = {"dealer": CONTACTS_FILTER_DEALERS, "deleted": CONTACTS_FILTER_REMOVED}
        return legacy_map[normalized]
    if normalized not in CONTACTS_FILTERS:
        return DEFAULT_CONTACTS_FILTER
    return normalized


def normalize_search_query(value: str | None) -> str:
    """Normalize a people/dealer/client search query."""
    return (value or "").strip()


def normalize_search_phone(value: str | None) -> str:
    """Normalize phone/WhatsApp values for digit-only matching."""
    return re.sub(r"\D", "", value or "")


def matches_contact_search(record: Record, query: str) -> bool:
    """Return True when a contact row matches a name or phone search query."""
    normalized_query = normalize_search_query(query).lower()
    if not normalized_query:
        return True

    searchable_values = [
        str(record.get("name") or ""),
        str(record.get("display_name") or ""),
        str(record.get("whatsapp_id") or ""),
        str(record.get("phone_number") or ""),
    ]
    query_digits = normalize_search_phone(normalized_query)

    for value in searchable_values:
        lowered_value = value.lower()
        if normalized_query in lowered_value:
            return True
        if query_digits and query_digits in normalize_search_phone(value):
            return True
    return False


def filter_records_by_contact_search(records: list[Record], query: str) -> list[Record]:
    """Filter contact-like records by name or phone/WhatsApp."""
    if not normalize_search_query(query):
        return records
    return [record for record in records if matches_contact_search(record, query)]


def matches_dealer_list_row_search(row: Record, query: str) -> bool:
    """Return True when a trader dealer list row matches a search query."""
    if matches_contact_search(row, query):
        return True
    normalized_query = normalize_search_query(query).lower()
    if not normalized_query:
        return True
    groups = str(row.get("groups") or "")
    if normalized_query in groups.lower():
        return True
    last_group = str(row.get("last_group") or "")
    return normalized_query in last_group.lower()


def filter_dealer_list_rows_by_search(rows: list[Record], query: str) -> list[Record]:
    """Filter trader dealer list rows by name, phone, WhatsApp, or group."""
    if not normalize_search_query(query):
        return rows
    return [row for row in rows if matches_dealer_list_row_search(row, query)]


def is_active_contacts_row(row: Record) -> bool:
    """Return True when a contact belongs on the default active people view."""
    contact_type = str(row.get("contact_type") or "")
    return contact_type in {CONTACT_TYPE_DEALER, CONTACT_TYPE_CLIENT}


def filter_contact_rows(
    rows: list[Record],
    *,
    filter_key: str = DEFAULT_CONTACTS_FILTER,
    search_query: str = "",
) -> list[Record]:
    """Filter contacts for the people management page."""
    normalized_filter = parse_contacts_filter(filter_key)
    visible = [
        row
        for row in rows
        if row.get("whatsapp_id") != IMPORT_PLACEHOLDER_WHATSAPP_ID
    ]

    if normalized_filter == CONTACTS_FILTER_REMOVED:
        filtered = [row for row in visible if row.get("contact_type") == CONTACT_TYPE_REMOVED]
    else:
        visible = [
            row
            for row in visible
            if row.get("contact_type") != CONTACT_TYPE_REMOVED
        ]

        if normalized_filter == CONTACTS_FILTER_ALL:
            filtered = visible
        elif normalized_filter == CONTACTS_FILTER_DEALERS:
            filtered = [row for row in visible if row.get("contact_type") == CONTACT_TYPE_DEALER]
        elif normalized_filter == CONTACTS_FILTER_CLIENTS:
            filtered = [row for row in visible if row.get("contact_type") == CONTACT_TYPE_CLIENT]
        else:
            filtered = [row for row in visible if is_active_contacts_row(row)]

    return filter_records_by_contact_search(filtered, search_query)


def build_contacts_filter_options(active_filter: str, search_query: str = "") -> list[Record]:
    """Build filter links for the contacts page."""
    normalized_filter = parse_contacts_filter(active_filter)
    normalized_search = normalize_search_query(search_query)

    def build_href(filter_key: str) -> str:
        params: list[str] = []
        if filter_key != DEFAULT_CONTACTS_FILTER:
            params.append(f"filter={filter_key}")
        if normalized_search:
            params.append(f"q={quote(normalized_search)}")
        if not params:
            return "/contacts"
        return f"/contacts?{'&'.join(params)}"

    return [
        {
            "key": filter_key,
            "label": CONTACTS_FILTER_LABELS[filter_key],
            "active": filter_key == normalized_filter,
            "href": build_href(filter_key),
        }
        for filter_key in CONTACTS_FILTERS
    ]
