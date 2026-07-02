"""Main navigation groups for MRV4ULT AI."""

from __future__ import annotations

from typing import Any

from permissions import can_manage_team, can_view_page, is_admin

Record = dict[str, Any]

NAV_GROUPS: tuple[Record, ...] = (
    {
        "key": "trading",
        "label": "Trading",
        "admin_only": False,
        "links": (
            {"label": "Dashboard", "path": "/dashboard", "match": "exact"},
            {"label": "Activity", "path": "/activity", "match": "prefix"},
            {
                "label": "Notifications",
                "path": "/notifications",
                "match": "prefix",
                "badge": "notifications",
            },
        ),
    },
    {
        "key": "market",
        "label": "Market",
        "admin_only": False,
        "links": (
            {"label": "Search", "path": "/", "match": "exact"},
            {"label": "Market Requests", "path": "/market-requests", "match": "prefix"},
            {"label": "Client Requests", "path": "/requests", "match": "prefix"},
        ),
    },
    {
        "key": "ai",
        "label": "AI",
        "admin_only": True,
        "links": (
            {"label": "AI Health", "path": "/ai-health", "match": "prefix"},
            {"label": "AI Workbench", "path": "/parser-review", "match": "prefix"},
            {"label": "Unknown Brands", "path": "/knowledge/unknown-brands", "match": "prefix"},
            {"label": "Unknown Nicknames", "path": "/knowledge/unknown-nicknames", "match": "prefix"},
        ),
    },
    {
        "key": "network",
        "label": "Network",
        "admin_only": False,
        "links": (
            {"label": "Dealers", "path": "/dealers", "match": "prefix"},
            {"label": "Clients", "path": "/clients", "match": "prefix"},
            {"label": "People", "path": "/contacts", "match": "prefix"},
        ),
    },
    {
        "key": "admin",
        "label": "Admin",
        "admin_only": True,
        "links": (
            {"label": "Team", "path": "/settings/team", "match": "prefix", "requires_team_admin": True},
            {"label": "WhatsApp", "path": "/whatsapp", "match": "prefix"},
            {"label": "Performance Profile", "path": "/performance-profile", "match": "exact"},
            {"label": "Import", "path": "/import", "match": "prefix", "admin_only": True},
        ),
    },
)


def nav_current_path(request: Any = None) -> str:
    """Return the current URL path for navigation active-state checks."""
    if isinstance(request, str):
        return request
    if request is None:
        return ""

    url = getattr(request, "url", None)
    if url is not None:
        path = getattr(url, "path", None)
        if isinstance(path, str):
            return path

    path = getattr(request, "path", None)
    if isinstance(path, str):
        return path
    return ""


def nav_item_visible(user: Record | None, item: Record) -> bool:
    """Return True when the current user may see a navigation item."""
    if item.get("admin_only") and not is_admin(user):
        return False
    if item.get("requires_team_admin") and not can_manage_team(user):
        return False
    return can_view_page(user, str(item["path"]))


def visible_nav_groups(user: Record | None) -> list[Record]:
    """Return navigation groups with only accessible items."""
    groups: list[Record] = []
    for group in NAV_GROUPS:
        if group.get("admin_only") and not is_admin(user):
            continue
        items = [dict(link) for link in group["links"] if nav_item_visible(user, link)]
        if items:
            groups.append(
                {
                    "key": group["key"],
                    "label": group["label"],
                    "links": items,
                }
            )
    return groups


def nav_item_active(item: Record, current_path: Any) -> bool:
    """Return True when the current path matches a navigation item."""
    path = nav_current_path(current_path)
    item_path = str(item["path"])
    match = item.get("match", "prefix")
    if match == "exact":
        return path == item_path
    return path.startswith(item_path) and not (item_path == "/" and path != "/")


def nav_group_active(group: Record, current_path: Any) -> bool:
    """Return True when the current path belongs to a navigation group."""
    path = nav_current_path(current_path)
    return any(nav_item_active(link, path) for link in group["links"])
