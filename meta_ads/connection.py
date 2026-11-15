"""Read only the connection and scopes the host authorized for this invocation."""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any
from .errors import AdsError
from .validation import identifier

REQUIRED_SCOPES = frozenset(
    {
        "ads_management",
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_ads",
        "instagram_basic",
    }
)


@dataclass(frozen=True)
class Connection:
    connection_id: str
    account_id: str
    token: str = field(repr=False)

    @property
    def account_path(self) -> str:
        return "act_" + self.account_id


def mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def connection_from_payload(
    payload: dict[str, Any], connection_ref: str, *, require_verified: bool = True
) -> Connection:
    action = mapping(payload.get("action"))
    target = mapping(action.get("target"))
    connection = mapping(target.get("connection"))
    if (
        connection.get("connection_id") != connection_ref
        or connection.get("connection_type", connection.get("connection_type_id"))
        != "meta_ads_account"
    ):
        raise AdsError("invalid_connection", "The selected Meta connection does not match")
    config = mapping(connection.get("config"))
    if set(config) - {"ad_account_id", "bound_ad_account_id", "oauth"}:
        raise AdsError("invalid_connection", "The Meta connection has unsupported configuration")
    account_id = (
        identifier(config.get("ad_account_id"), "connection advertising account")
        if require_verified or config.get("ad_account_id")
        else ""
    )
    oauth = mapping(config.get("oauth"))
    anchor = config.get("bound_ad_account_id")
    if require_verified and (anchor != account_id or oauth.get("lifecycle_status") != "connected"):
        raise AdsError(
            "invalid_connection",
            "Authorize this advertising account; use a new connection to change accounts",
        )
    token = mapping(connection.get("secrets")).get("access_token")
    if (
        not isinstance(token, str)
        or not token
        or len(token) > 8192
        or any(ord(c) < 33 or ord(c) > 126 for c in token)
    ):
        raise AdsError("authorization_required", "Reconnect the Meta account")
    return Connection(connection_ref, account_id, token)


def verify_permissions(client: Any) -> None:
    granted: set[str] = set()
    cursor = None
    for _ in range(20):
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["after"] = cursor
        page = client.request("GET", "me/permissions", params)
        rows = page.get("data")
        if not isinstance(rows, list):
            raise AdsError("provider_unavailable", "Meta permissions response is invalid")
        for row in rows:
            if not isinstance(row, dict) or row.get("status") != "granted":
                continue
            permission = row.get("permission")
            if isinstance(permission, str):
                granted.add(permission)
        paging = mapping(page.get("paging"))
        if not paging.get("next"):
            break
        new = mapping(paging.get("cursors")).get("after")
        if not isinstance(new, str) or not new or new == cursor:
            raise AdsError("provider_unavailable", "Meta permissions pagination is invalid")
        cursor = new
    else:
        raise AdsError("provider_unavailable", "Meta permissions pagination exceeds the limit")
    if not REQUIRED_SCOPES.issubset(granted):
        raise AdsError("permission_denied", "Authorize all required Meta Ads permissions")
