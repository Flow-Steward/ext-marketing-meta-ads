"""OAuth identity and a one-time account binding through native host mutations."""

from __future__ import annotations
from collections.abc import Callable
from typing import Any
from flowsteward_extension_sdk import RuntimeResources, host_mutation
from .connection import connection_from_payload, mapping, verify_permissions
from .errors import AdsError
from .operations import account
from .validation import CURRENCY_EXPONENTS, closed, identifier, text


def verify_oauth(payload: dict[str, Any], factory: Callable[..., Any]) -> dict[str, Any]:
    connection = mapping(mapping(mapping(payload.get("action")).get("target")).get("connection"))
    token = mapping(connection.get("secrets")).get("access_token")
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 8192
        or any(ord(c) < 33 or ord(c) > 126 for c in token)
    ):
        raise AdsError("authorization_required", "Reconnect the Meta account")
    client = factory(token)
    verify_permissions(client)
    user = client.request("GET", "me", {"fields": "id"})
    subject = identifier(user.get("id"), "Meta user ID")
    return {
        "ok": True,
        "result": {
            "subject": subject,
            "verified_display_name": "Meta user " + subject,
            "safe_metadata": {},
            "health": "ok",
        },
    }


def bind_account(payload: dict[str, Any], factory: Callable[..., Any]) -> dict[str, Any]:
    action = mapping(payload.get("action"))
    inputs = closed(
        action.get("input"),
        {"connection_ref", "ad_account_id"},
        {"connection_ref", "ad_account_id"},
    )
    ref = text(inputs["connection_ref"], "connection_ref", maximum=128)
    desired = identifier(inputs["ad_account_id"], "ad_account_id")
    connection = connection_from_payload(payload, ref, require_verified=False)
    context = mapping(payload.get("runtime_context"))
    resource = RuntimeResources.from_context(context).object("project_connection")
    if resource.get("connection_id", resource.get("id")) != ref:
        raise AdsError("invalid_connection", "The selected connection configuration is unavailable")
    config = mapping(resource.get("connection_config"))
    if "bound_ad_account_id" in config and config["bound_ad_account_id"] != desired:
        raise AdsError(
            "invalid_connection", "Create a new connection for another advertising account"
        )
    if mapping(config.get("oauth")).get("lifecycle_status") != "connected":
        raise AdsError(
            "authorization_required", "Complete Meta authorization before selecting an account"
        )
    if context.get("test_mode") is True:
        return {
            "ok": True,
            "result": {"test_mode_status": "suppressed"},
            "definitely_no_external_effect": True,
        }
    client = factory(connection.token)
    verify_permissions(client)
    selected = type(connection)(connection.connection_id, desired, connection.token)
    metadata = account(client, selected)
    if metadata.get("currency") not in CURRENCY_EXPONENTS:
        raise AdsError(
            "unsupported_configuration", "This advertising account currency is unsupported"
        )
    if not isinstance(metadata.get("timezone_name"), str) or not metadata["timezone_name"]:
        raise AdsError("provider_unavailable", "Meta omitted the advertising account timezone")
    return {
        "ok": True,
        "result": {
            "ad_account_id": desired,
            "currency": metadata["currency"],
            "timezone": metadata["timezone_name"],
            "bound": True,
        },
        "host_mutations": [
            host_mutation(
                "project_connection_config_compare_and_set",
                {
                    "connection_id": ref,
                    "expect": [
                        {
                            "path": "bound_ad_account_id",
                            "state": "absent_or_equals",
                            "value": desired,
                        }
                    ],
                    "set": {"ad_account_id": desired, "bound_ad_account_id": desired},
                },
            )
        ],
    }
