"""One explicit operation per workflow action; never orchestrate whole campaigns here."""

from __future__ import annotations
import base64
import re
from typing import Any
from . import objects
from .connection import Connection, mapping
from .errors import AdsError, invalid
from .images import read_image
from .validation import ad_set_input, campaign_input, creative_input, identifier, text

PARAMETERS = {
    "list_ad_accounts": ({"cursor", "limit"}, set()),
    "list_ad_identities": ({"cursor", "limit"}, set()),
    "create_campaign": ({"name", "ad_category"}, {"name", "ad_category"}),
    "create_ad_set": (
        {
            "campaign_id",
            "name",
            "countries",
            "age_min",
            "age_max",
            "daily_budget",
            "start_time",
            "end_time",
            "placements",
            "dsa_beneficiary",
            "dsa_payor",
        },
        {
            "campaign_id",
            "name",
            "countries",
            "age_min",
            "age_max",
            "daily_budget",
            "start_time",
            "end_time",
            "placements",
        },
    ),
    "upload_image": ({"image_artifact"}, {"image_artifact"}),
    "create_creative": (
        {
            "name",
            "image_hash",
            "page_id",
            "instagram_account_id",
            "placements",
            "primary_text",
            "headline",
            "destination_url",
            "cta",
            "utm",
        },
        {"name", "image_hash", "page_id", "placements", "primary_text", "destination_url", "cta"},
    ),
    "create_ad": ({"name", "ad_set_id", "creative_id"}, {"name", "ad_set_id", "creative_id"}),
    "set_delivery_status": (
        {"object_type", "object_id", "status"},
        {"object_type", "object_id", "status"},
    ),
    "get_object_status": ({"object_type", "object_id"}, {"object_type", "object_id"}),
}
WRITES = frozenset(
    set(PARAMETERS) - {"list_ad_accounts", "list_ad_identities", "get_object_status"}
)


def account(client: Any, connection: Connection) -> dict[str, Any]:
    row = client.request(
        "GET",
        connection.account_path,
        {"fields": "id,account_id,name,currency,timezone_name,account_status"},
    )
    if row.get("account_id") != connection.account_id:
        raise AdsError("invalid_connection", "Meta returned another advertising account")
    if row.get("account_status") != 1:
        raise AdsError("unsupported_configuration", "The advertising account is not active")
    return row


def pagination(value: dict[str, Any]) -> dict[str, Any]:
    result = {"limit": value.get("limit", 50)}
    if type(result["limit"]) is not int or not 1 <= result["limit"] <= 100:
        raise invalid("limit")
    if "cursor" in value:
        cursor = text(value["cursor"], "cursor", maximum=2048)
        if re.fullmatch(r"[A-Za-z0-9_+/.=:-]+", cursor) is None:
            raise invalid("cursor")
        result["after"] = cursor
    return result


def next_cursor(page: dict[str, Any]) -> str | None:
    paging = mapping(page.get("paging"))
    if not paging.get("next"):
        return None
    value = mapping(paging.get("cursors")).get("after")
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise AdsError("provider_unavailable", "Meta omitted the next-page cursor")
    return value


def identities(
    client: Any, connection: Connection, params: dict[str, Any]
) -> tuple[list[dict[str, Any]], str | None]:
    page = client.request(
        "GET",
        connection.account_path + "/promote_pages",
        {**params, "fields": "id,name,instagram_business_account{id,username}"},
    )
    rows = page.get("data")
    if not isinstance(rows, list):
        raise AdsError("provider_unavailable", "Meta returned invalid Page identities")
    result = []
    for item in rows:
        if not isinstance(item, dict):
            raise AdsError("provider_unavailable", "Meta returned invalid Page identities")
        page_id = identifier(item.get("id"), "Meta Page ID")
        ig = mapping(item.get("instagram_business_account"))
        result.append(
            {
                "page_id": page_id,
                "page_name": str(item.get("name") or "")[:255],
                "instagram_account_id": ig.get("id"),
                "instagram_username": str(ig.get("username") or "")[:255] or None,
            }
        )
    return result, next_cursor(page)


def verify_identity_pair(
    client: Any, connection: Connection, page_id: str, ig_id: str | None
) -> None:
    cursor = None
    for _ in range(100):
        params: dict[str, Any] = {"limit": 100}
        if cursor:
            params["after"] = cursor
        rows, cursor = identities(client, connection, params)
        for row in rows:
            if row["page_id"] == page_id:
                if ig_id and row["instagram_account_id"] != ig_id:
                    raise AdsError(
                        "invalid_connection", "Instagram identity does not match the selected Page"
                    )
                if ig_id:
                    _verify_account_instagram(client, connection, ig_id)
                return
        if cursor is None:
            break
    raise AdsError("invalid_connection", "The Page is not available to this advertising account")


def _verify_account_instagram(client: Any, connection: Connection, ig_id: str) -> None:
    cursor = None
    for _ in range(100):
        params = {"limit": 100, "fields": "id"}
        if cursor:
            params["after"] = cursor
        page = client.request("GET", connection.account_path + "/instagram_accounts", params)
        if not isinstance(page.get("data"), list):
            raise AdsError("provider_unavailable", "Meta Instagram response is invalid")
        if any(isinstance(row, dict) and row.get("id") == ig_id for row in page["data"]):
            return
        cursor = next_cursor(page)
        if cursor is None:
            break
    raise AdsError(
        "invalid_connection", "The Instagram account is unavailable to this advertising account"
    )


def _created(
    client: Any, path: str, body: dict[str, Any], key: str, connection: Connection, **extra: Any
) -> dict[str, Any]:
    response = client.request("POST", path, data=body)
    try:
        object_id = identifier(response.get("id"), "Meta creation result ID")
    except AdsError:
        raise AdsError(
            "timeout_unknown", "Meta did not confirm the created object ID", ambiguous=True
        ) from None
    return {
        key: object_id,
        "ad_account_id": connection.account_id,
        **extra,
        **({"configured_status": "PAUSED", "effective_status": None} if "status" in body else {}),
    }


def execute(
    client: Any,
    connection: Connection,
    op: str,
    inputs: dict[str, Any],
    payload: dict[str, Any],
    *,
    image_reader: Any = read_image,
) -> dict[str, Any]:
    if op == "list_ad_accounts":
        page = client.request(
            "GET",
            "me/adaccounts",
            {
                **pagination(inputs),
                "fields": "id,account_id,name,currency,timezone_name,account_status",
            },
        )
        if not isinstance(page.get("data"), list):
            raise AdsError("provider_unavailable", "Meta returned invalid advertising accounts")
        rows = []
        for row in page["data"]:
            if not isinstance(row, dict):
                raise AdsError("provider_unavailable", "Meta returned invalid advertising accounts")
            aid = identifier(row.get("account_id"), "Meta account ID")
            rows.append(
                {
                    "ad_account_id": aid,
                    "name": str(row.get("name") or "")[:255],
                    "currency": row.get("currency"),
                    "timezone": row.get("timezone_name"),
                    "selected": aid == connection.account_id,
                }
            )
        return {"accounts": rows, "next_cursor": next_cursor(page)}
    if op == "list_ad_identities":
        rows, cursor = identities(client, connection, pagination(inputs))
        return {"identities": rows, "next_cursor": cursor}
    if op in {"get_object_status", "set_delivery_status"}:
        return _status(client, connection, op, inputs)
    if op == "create_campaign":
        body = campaign_input(inputs)
        account(client, connection)
        return _created(
            client, connection.account_path + "/campaigns", body, "campaign_id", connection
        )
    if op == "create_ad_set":
        meta = account(client, connection)
        body = ad_set_input(inputs, str(meta.get("currency") or ""))
        objects.campaign(client, connection, body["campaign_id"])
        return _created(
            client,
            connection.account_path + "/adsets",
            body,
            "ad_set_id",
            connection,
            campaign_id=body["campaign_id"],
        )
    if op == "upload_image":
        artifact = text(inputs["image_artifact"], "image_artifact", maximum=512)
        raw = image_reader(payload, artifact)
        account(client, connection)
        response = client.request(
            "POST",
            connection.account_path + "/adimages",
            data={"bytes": base64.b64encode(raw).decode("ascii")},
        )
        images = mapping(response.get("images"))
        values = list(images.values())
        image_hash = mapping(values[0]).get("hash") if len(values) == 1 else None
        if not isinstance(image_hash, str) or re.fullmatch(r"[a-fA-F0-9]{32}", image_hash) is None:
            raise AdsError(
                "timeout_unknown", "Meta did not confirm the uploaded image", ambiguous=True
            )
        return {"image_hash": image_hash, "ad_account_id": connection.account_id}
    if op == "create_creative":
        body = creative_input(inputs)
        account(client, connection)
        verify_identity_pair(
            client, connection, inputs["page_id"], inputs.get("instagram_account_id")
        )
        _image_owned(client, connection, inputs["image_hash"])
        return _created(
            client, connection.account_path + "/adcreatives", body, "creative_id", connection
        )
    if op == "create_ad":
        name = text(inputs["name"], "name")
        group = objects.group(client, connection, identifier(inputs["ad_set_id"], "ad_set_id"))
        creative = _compatible_creative(
            client,
            connection,
            identifier(inputs["creative_id"], "creative_id"),
            objects.placements_for(group["targeting"]),
        )
        body = {
            "name": name,
            "adset_id": group["id"],
            "creative": {"creative_id": creative["id"]},
            "status": "PAUSED",
        }
        return _created(
            client,
            connection.account_path + "/ads",
            body,
            "ad_id",
            connection,
            ad_set_id=group["id"],
            campaign_id=group["campaign_id"],
        )
    raise invalid("operation")


def _image_owned(client: Any, connection: Connection, image_hash: str) -> None:
    page = client.request(
        "GET",
        connection.account_path + "/adimages",
        {"hashes": [image_hash], "fields": "hash,width,height"},
    )
    rows = page.get("data")
    if not isinstance(rows, list) or not any(
        isinstance(row, dict)
        and row.get("hash") == image_hash
        and row.get("width") == 1080
        and row.get("height") == 1080
        for row in rows
    ):
        raise AdsError(
            "invalid_connection", "The image is not a supported image in this advertising account"
        )


def _compatible_creative(
    client: Any, connection: Connection, creative_id: str, placements: str
) -> dict[str, Any]:
    row = objects.owned(client, connection, "creative", creative_id)
    if row.get("asset_feed_spec") or row.get("degrees_of_freedom_spec"):
        raise objects.unsupported()
    story = mapping(row.get("object_story_spec"))
    link = mapping(story.get("link_data"))
    if set(story) - {"page_id", "instagram_user_id", "link_data"} or set(link) - {
        "image_hash",
        "message",
        "link",
        "name",
        "call_to_action",
    }:
        raise objects.unsupported()
    call = mapping(link.get("call_to_action"))
    values: dict[str, Any] = {
        "name": "Validate existing creative",
        "image_hash": link.get("image_hash"),
        "page_id": story.get("page_id"),
        "placements": placements,
        "primary_text": link.get("message"),
        "destination_url": link.get("link"),
        "cta": call.get("type"),
    }
    if story.get("instagram_user_id"):
        values["instagram_account_id"] = story["instagram_user_id"]
    if link.get("name"):
        values["headline"] = link["name"]
    try:
        creative_input(values)
    except AdsError:
        raise objects.unsupported() from None
    verify_identity_pair(client, connection, values["page_id"], values.get("instagram_account_id"))
    _image_owned(client, connection, values["image_hash"])
    return row


def _status(client: Any, connection: Connection, op: str, inputs: dict[str, Any]) -> dict[str, Any]:
    kind = inputs["object_type"]
    if not isinstance(kind, str) or kind not in {"campaign", "ad_set", "ad"}:
        raise invalid("object_type")
    oid = identifier(inputs["object_id"], "object_id")
    row = objects.owned(client, connection, kind, oid)
    if op == "get_object_status":
        return objects.status_result(row, kind, connection.account_id)
    if inputs["status"] not in {"ACTIVE", "PAUSED"}:
        raise invalid("status")
    account(client, connection)
    if kind == "campaign":
        objects.campaign(client, connection, oid)
    elif kind == "ad_set":
        objects.group(client, connection, oid)
    else:
        group = objects.group(client, connection, identifier(row.get("adset_id"), "adset_id"))
        creative = mapping(row.get("creative")).get("id")
        if creative:
            _compatible_creative(
                client,
                connection,
                identifier(creative, "creative_id"),
                objects.placements_for(group["targeting"]),
            )
    response = client.request("POST", oid, data={"status": inputs["status"]})
    if response.get("success") is not True:
        raise AdsError("timeout_unknown", "Meta did not confirm the status update", ambiguous=True)
    # The write is confirmed. A failing read must not discard that fact or its IDs.
    try:
        current = objects.owned(client, connection, kind, oid)
    except AdsError:
        current = {
            "id": oid,
            "status": None,
            "effective_status": None,
            **{k: row[k] for k in ["campaign_id", "adset_id"] if k in row},
        }
    result = objects.status_result(current, kind, connection.account_id)
    result["requested_status"] = inputs["status"]
    return result
