"""Evolution API v2 client for MRV4ULT AI WhatsApp instance management."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

import httpx
from dotenv import load_dotenv

load_dotenv()

Record = dict[str, Any]

DEFAULT_INSTANCE_NAME = "mrv4ult"


class EvolutionAPIError(Exception):
    """Raised when Evolution API returns an error response."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        payload: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.payload = payload


def get_evolution_url() -> str:
    url = os.environ.get("EVOLUTION_URL", "http://localhost:8080").strip()
    if not url:
        raise EvolutionAPIError("EVOLUTION_URL is not set.")
    return url.rstrip("/")


def get_authentication_api_key() -> str:
    api_key = os.environ.get("AUTHENTICATION_API_KEY", "").strip()
    if not api_key:
        raise EvolutionAPIError("AUTHENTICATION_API_KEY is not set.")
    return api_key


def get_default_instance_name() -> str:
    return os.environ.get("EVOLUTION_INSTANCE_NAME", DEFAULT_INSTANCE_NAME).strip() or DEFAULT_INSTANCE_NAME


def _headers() -> dict[str, str]:
    return {
        "apikey": get_authentication_api_key(),
        "Content-Type": "application/json",
    }


def _request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    url = f"{get_evolution_url()}{path}"
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.request(
                method,
                url,
                headers=_headers(),
                params=params,
                json=json,
            )
    except httpx.RequestError as exc:
        raise EvolutionAPIError(f"Could not reach Evolution API at {url}: {exc}") from exc

    if response.status_code >= 400:
        payload: Any
        try:
            payload = response.json()
        except ValueError:
            payload = response.text
        message = _extract_error_message(payload) or response.text or "Evolution API request failed."
        raise EvolutionAPIError(message, status_code=response.status_code, payload=payload)

    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError:
        return {"raw": response.text}


def _extract_error_message(payload: Any) -> str | None:
    if isinstance(payload, dict):
        for key in ("message", "error", "response"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, dict):
                nested = value.get("message")
                if isinstance(nested, str) and nested.strip():
                    return nested
    return None


def fetch_instances(instance_name: str | None = None) -> list[Record]:
    """Return instances from GET /instance/fetchInstances."""
    params = {"instanceName": instance_name} if instance_name else None
    payload = _request("GET", "/instance/fetchInstances", params=params)

    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("instances", "instance", "data"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                return [value]
    return []


def instance_exists(instance_name: str) -> bool:
    return bool(fetch_instances(instance_name))


def create_instance(instance_name: str) -> Record:
    """Create a Baileys instance via POST /instance/create."""
    return _request(
        "POST",
        "/instance/create",
        json={
            "instanceName": instance_name,
            "integration": "WHATSAPP-BAILEYS",
            "qrcode": True,
        },
    )


def get_qr_code(instance_name: str) -> Record:
    """Fetch a QR code via GET /instance/connect/{instance}."""
    return _request("GET", f"/instance/connect/{instance_name}")


def get_instance_status(instance_name: str) -> Record:
    """Return normalized connection status for an instance."""
    instances = fetch_instances(instance_name)
    instance_record = instances[0] if instances else {}
    exists = bool(instance_record)

    state = "close"
    if exists:
        try:
            payload = _request("GET", f"/instance/connectionState/{instance_name}")
            state = ((payload.get("instance") or {}).get("state") or state).lower()
        except EvolutionAPIError:
            state = _state_from_instance_record(instance_record)

    return {
        "instance_name": instance_name,
        "exists": exists,
        "state": state,
        "connected": state == "open",
        "phone_number": _extract_phone_number(instance_record),
        "profile_name": instance_record.get("profileName"),
        "last_connection_time": _format_timestamp(
            instance_record.get("updatedAt")
            or instance_record.get("createdAt")
        ),
        "status_label": _format_state_label(state),
    }


def get_whatsapp_page_state(instance_name: str | None = None) -> Record:
    """Build dashboard state including QR code when connection is pending."""
    name = instance_name or get_default_instance_name()
    status = get_instance_status(name)

    if not status["exists"] or status["connected"]:
        status["qr_base64"] = None
        return status

    try:
        status["qr_base64"] = extract_qr_base64(get_qr_code(name))
    except EvolutionAPIError:
        status["qr_base64"] = None

    return status


def extract_qr_base64(payload: Record) -> str | None:
    """Extract a data-URI QR image from an Evolution API response."""
    qrcode = payload.get("qrcode")
    if isinstance(qrcode, dict):
        base64_value = qrcode.get("base64")
        if isinstance(base64_value, str) and base64_value.strip():
            return _normalize_base64_image(base64_value)

    base64_value = payload.get("base64")
    if isinstance(base64_value, str) and base64_value.strip():
        return _normalize_base64_image(base64_value)

    return None


def _normalize_base64_image(value: str) -> str:
    cleaned = value.strip()
    if cleaned.startswith("data:image"):
        return cleaned
    return f"data:image/png;base64,{cleaned}"


def _state_from_instance_record(record: Record) -> str:
    connection_status = record.get("connectionStatus")
    if isinstance(connection_status, dict):
        for key in ("state", "status"):
            value = connection_status.get(key)
            if isinstance(value, str):
                return value.lower()
    if isinstance(connection_status, str):
        normalized = connection_status.lower()
        if normalized in {"open", "connecting", "close", "online", "offline"}:
            return "open" if normalized == "online" else "close" if normalized == "offline" else normalized
    return "close"


def _extract_phone_number(record: Record) -> str | None:
    number = record.get("number")
    if isinstance(number, str) and number.strip():
        return number.strip()

    owner_jid = record.get("ownerJid")
    if isinstance(owner_jid, str) and owner_jid.strip():
        phone = owner_jid.split("@", 1)[0].strip()
        return phone or None

    return None


def _format_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    try:
        timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return timestamp.strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def _format_state_label(state: str) -> str:
    labels = {
        "open": "Connected",
        "connecting": "Waiting for QR scan",
        "close": "Disconnected",
    }
    return labels.get(state, state.title())
