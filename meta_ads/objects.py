"""Provider projections and compatibility rules for existing advertising objects."""

from __future__ import annotations
from typing import Any
from .connection import Connection, mapping
from .errors import AdsError, invalid
from .validation import COUNTRIES, DSA_COUNTRIES, identifier, placement_target

FIELDS = {
    "campaign": "id,account_id,name,objective,special_ad_categories,status,effective_status,daily_budget,lifetime_budget,is_adset_budget_sharing_enabled,issues_info",
    "ad_set": "id,account_id,campaign_id,status,effective_status,daily_budget,lifetime_budget,optimization_goal,billing_event,bid_strategy,destination_type,targeting,start_time,end_time,dsa_beneficiary,dsa_payor,issues_info",
    "ad": "id,account_id,campaign_id,adset_id,status,effective_status,creative{id},issues_info,ad_review_feedback",
    "creative": "id,account_id,object_story_spec,url_tags,asset_feed_spec,degrees_of_freedom_spec,status",
}


def unsupported() -> AdsError:
    return AdsError(
        "unsupported_configuration", "The existing object does not match the Meta Ads v1 profile"
    )


def owned(client: Any, connection: Connection, kind: str, object_id: str) -> dict[str, Any]:
    if kind not in FIELDS:
        raise invalid("object_type")
    row = client.request("GET", identifier(object_id, "object_id"), {"fields": FIELDS[kind]})
    if row.get("id") != object_id or row.get("account_id") != connection.account_id:
        raise AdsError(
            "invalid_connection", "The object does not belong to this advertising account"
        )
    if row.get("status") in {"DELETED", "ARCHIVED"}:
        raise unsupported()
    return row


def campaign(client: Any, connection: Connection, object_id: str) -> dict[str, Any]:
    row = owned(client, connection, "campaign", object_id)
    if row.get("objective") != "OUTCOME_TRAFFIC" or row.get("special_ad_categories") not in (
        [],
        ["NONE"],
    ):
        raise unsupported()
    if (
        str(row.get("daily_budget") or "0") != "0"
        or str(row.get("lifetime_budget") or "0") != "0"
        or row.get("is_adset_budget_sharing_enabled") not in (None, False)
    ):
        raise unsupported()
    return row


def group(client: Any, connection: Connection, object_id: str) -> dict[str, Any]:
    row = owned(client, connection, "ad_set", object_id)
    campaign(client, connection, identifier(row.get("campaign_id"), "campaign_id"))
    if (
        row.get("optimization_goal") != "LINK_CLICKS"
        or row.get("billing_event") != "IMPRESSIONS"
        or row.get("bid_strategy") != "LOWEST_COST_WITHOUT_CAP"
        or row.get("destination_type") != "WEBSITE"
    ):
        raise unsupported()
    daily = row.get("daily_budget")
    if (
        not isinstance(daily, str)
        or not daily.isdigit()
        or int(daily) <= 0
        or str(row.get("lifetime_budget") or "0") != "0"
    ):
        raise unsupported()
    target = mapping(row.get("targeting"))
    allowed = {
        "geo_locations",
        "age_min",
        "age_max",
        "publisher_platforms",
        "facebook_positions",
        "instagram_positions",
    }
    if set(target) - allowed:
        raise unsupported()
    geo = mapping(target.get("geo_locations"))
    countries = geo.get("countries")
    if (
        set(geo) != {"countries"}
        or not isinstance(countries, list)
        or not countries
        or any(not isinstance(c, str) or c not in COUNTRIES for c in countries)
    ):
        raise unsupported()
    low, high = target.get("age_min"), target.get("age_max")
    if type(low) is not int or type(high) is not int or not 18 <= low <= high <= 65:
        raise unsupported()
    if set(countries) & DSA_COUNTRIES and not all(
        isinstance(row.get(k), str) and row[k].strip() for k in ["dsa_beneficiary", "dsa_payor"]
    ):
        raise unsupported()
    placements_for(target)
    return row


def placements_for(target: dict[str, Any]) -> str:
    for name in ["facebook", "instagram", "both"]:
        expected = placement_target(name)
        actual = {
            k: v for k, v in target.items() if k.endswith("positions") or k == "publisher_platforms"
        }
        if actual == expected:
            return name
    raise unsupported()


def status_result(row: dict[str, Any], kind: str, account_id: str) -> dict[str, Any]:
    parents = {k: row[k] for k in ["campaign_id", "adset_id"] if isinstance(row.get(k), str)}
    issues = []
    for item in row.get("issues_info", []) if isinstance(row.get("issues_info"), list) else []:
        if isinstance(item, dict):
            # Provider text can echo user data or tokens. Return bounded codes only.
            issues.append(
                {
                    k: item[k]
                    for k in ["level", "error_code", "error_type"]
                    if isinstance(item.get(k), (str, int)) and not isinstance(item.get(k), bool)
                }
            )
    if row.get("ad_review_feedback"):
        issues.append({"kind": "ad_review_feedback_available"})
    return {
        "object_id": row["id"],
        "object_type": kind,
        "ad_account_id": account_id,
        "configured_status": row.get("configured_status", row.get("status")),
        "effective_status": row.get("effective_status"),
        "parents": parents,
        "issues": issues,
    }
