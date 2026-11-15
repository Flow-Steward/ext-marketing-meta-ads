"""Closed v1 inputs; pure transformations with no provider side effects."""

from __future__ import annotations
import re
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit
from .errors import AdsError, invalid

COUNTRIES = frozenset(
    "AD AE AF AG AI AL AM AO AQ AR AS AT AU AW AX AZ BA BB BD BE BF BG BH BI BJ BL BM BN BO BQ BR BS BT BV BW BY BZ CA CC CD CF CG CH CI CK CL CM CN CO CR CU CV CW CX CY CZ DE DJ DK DM DO DZ EC EE EG EH ER ES ET FI FJ FK FM FO FR GA GB GD GE GF GG GH GI GL GM GN GP GQ GR GS GT GU GW GY HK HM HN HR HT HU ID IE IL IM IN IO IQ IR IS IT JE JM JO JP KE KG KH KI KM KN KP KR KW KY KZ LA LB LC LI LK LR LS LT LU LV LY MA MC MD ME MF MG MH MK ML MM MN MO MP MQ MR MS MT MU MV MW MX MY MZ NA NC NE NF NG NI NL NO NP NR NU NZ OM PA PE PF PG PH PK PL PM PN PR PS PT PW PY QA RE RO RS RU RW SA SB SC SD SE SG SH SI SJ SK SL SM SN SO SR SS ST SV SX SY SZ TC TD TF TG TH TJ TK TL TM TN TO TR TT TV TW TZ UA UG UM US UY UZ VA VC VE VG VI VN VU WF WS YE YT ZA ZM ZW".split()
)
DSA_COUNTRIES = frozenset(
    "AT BE BG HR CY CZ DK EE FI FR DE GR HU IE IT LV LT LU MT NL PL PT RO SK SI ES SE IS LI NO AX GF GP MQ RE MF YT BL PM".split()
)
# Currency support is explicit; no conversion and no default exponent for unknown currencies.
CURRENCY_EXPONENTS = dict.fromkeys(
    "AED ARS AUD BDT BOB BRL CAD CHF CNY COP CRC CZK DKK DOP DZD EGP EUR GBP GTQ HKD HNL HRK HUF IDR ILS INR KES LKR MAD MOP MXN MYR NGN NIO NOK NZD PEN PHP PKR PLN QAR RON RSD RUB SAR SEK SGD THB TRY TWD UAH USD UYU ZAR".split(),
    2,
)
CURRENCY_EXPONENTS.update(dict.fromkeys("CLP ISK JPY KRW PYG VND".split(), 0))
PLACEMENTS = {
    "facebook": ["facebook"],
    "instagram": ["instagram"],
    "both": ["facebook", "instagram"],
}
UTM_KEYS = frozenset({"utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"})


def text(value: Any, field: str, *, maximum: int = 255) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > maximum
        or any(ord(c) < 32 and c not in "\n\t" for c in value)
    ):
        raise invalid(field)
    return value


def identifier(value: Any, field: str) -> str:
    if not isinstance(value, str) or re.fullmatch(r"[1-9][0-9]{0,31}", value) is None:
        raise invalid(field)
    return value


def closed(value: Any, allowed: set[str], required: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) - allowed or not required.issubset(value):
        raise invalid("operation input fields")
    return dict(value)


def budget_units(value: Any, currency: str) -> int:
    exponent = CURRENCY_EXPONENTS.get(currency)
    if exponent is None:
        raise AdsError(
            "unsupported_configuration", "This advertising account currency is unsupported"
        )
    if not isinstance(value, str) or re.fullmatch(r"[0-9]{1,12}(?:\.[0-9]+)?", value) is None:
        raise invalid("daily_budget")
    try:
        amount = Decimal(value)
        units = amount.scaleb(exponent)
    except InvalidOperation as exc:
        raise invalid("daily_budget") from exc
    precision = amount.as_tuple().exponent
    if not isinstance(precision, int):
        raise invalid("daily_budget")
    if amount <= 0 or units != units.to_integral_value() or precision < -exponent:
        raise invalid("daily_budget precision")
    if units > 10**12:
        raise invalid("daily_budget")
    return int(units)


def campaign_input(value: Any) -> dict[str, Any]:
    row = closed(value, {"name", "ad_category"}, {"name", "ad_category"})
    if row["ad_category"] != "general":
        raise AdsError("unsupported_configuration", "Only ordinary advertisements are supported")
    return {
        "name": text(row["name"], "name"),
        "objective": "OUTCOME_TRAFFIC",
        "special_ad_categories": [],
        "status": "PAUSED",
        "is_adset_budget_sharing_enabled": False,
    }


def placement_target(value: Any) -> dict[str, Any]:
    if not isinstance(value, str) or value not in PLACEMENTS:
        raise invalid("placements")
    platforms = PLACEMENTS[value]
    result: dict[str, Any] = {"publisher_platforms": platforms.copy()}
    if "facebook" in platforms:
        result["facebook_positions"] = ["feed"]
    if "instagram" in platforms:
        result["instagram_positions"] = ["stream"]
    return result


def _date(value: Any, field: str) -> datetime:
    raw = text(value, field, maximum=64)
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise invalid(field) from exc
    if "T" not in raw or parsed.tzinfo is None or parsed.utcoffset() is None:
        raise invalid(field)
    return parsed.astimezone(UTC)


def ad_set_input(value: Any, currency: str, *, now: datetime | None = None) -> dict[str, Any]:
    required = {
        "campaign_id",
        "name",
        "countries",
        "age_min",
        "age_max",
        "daily_budget",
        "start_time",
        "end_time",
        "placements",
    }
    row = closed(value, required | {"dsa_beneficiary", "dsa_payor"}, required)
    countries = row["countries"]
    if (
        not isinstance(countries, list)
        or not countries
        or len(countries) > 50
        or any(not isinstance(c, str) or c not in COUNTRIES for c in countries)
        or len(set(countries)) != len(countries)
    ):
        raise invalid("countries")
    for field in ["age_min", "age_max"]:
        if type(row[field]) is not int or not 18 <= row[field] <= 65:
            raise invalid(field)
    if row["age_min"] > row["age_max"]:
        raise invalid("age range")
    start, end = _date(row["start_time"], "start_time"), _date(row["end_time"], "end_time")
    if start <= (now or datetime.now(UTC)) or end <= start:
        raise invalid("schedule")
    target = {
        "geo_locations": {"countries": countries},
        "age_min": row["age_min"],
        "age_max": row["age_max"],
        **placement_target(row["placements"]),
    }
    body = {
        "campaign_id": identifier(row["campaign_id"], "campaign_id"),
        "name": text(row["name"], "name"),
        "daily_budget": budget_units(row["daily_budget"], currency),
        "start_time": start.isoformat(),
        "end_time": end.isoformat(),
        "targeting": target,
        "optimization_goal": "LINK_CLICKS",
        "billing_event": "IMPRESSIONS",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "destination_type": "WEBSITE",
        "status": "PAUSED",
    }
    for field in ["dsa_beneficiary", "dsa_payor"]:
        if set(countries) & DSA_COUNTRIES or field in row:
            body[field] = text(row.get(field), field)
    return body


def creative_input(value: Any) -> dict[str, Any]:
    required = {
        "name",
        "image_hash",
        "page_id",
        "placements",
        "primary_text",
        "destination_url",
        "cta",
    }
    row = closed(value, required | {"instagram_account_id", "headline", "utm"}, required)
    placement_target(row["placements"])
    image_hash = row["image_hash"]
    if not isinstance(image_hash, str) or re.fullmatch(r"[a-fA-F0-9]{32}", image_hash) is None:
        raise invalid("image_hash")
    url = text(row["destination_url"], "destination_url", maximum=2048)
    try:
        parsed = urlsplit(url)
        port = parsed.port
    except ValueError as exc:
        raise invalid("destination_url") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or port not in {None, 443}
        or any(c.isspace() for c in url)
        or any(ord(c) < 32 for c in url)
    ):
        raise invalid("destination_url")
    if not isinstance(row["cta"], str) or row["cta"] not in {"LEARN_MORE", "SHOP_NOW"}:
        raise invalid("cta")
    utm = row.get("utm", {})
    if not isinstance(utm, dict) or set(utm) - UTM_KEYS:
        raise invalid("utm")
    existing = {k for k, v in parse_qsl(parsed.query, keep_blank_values=True)}
    if set(utm) & existing:
        raise invalid("UTM collision with destination URL")
    tags = {key: text(val, "utm", maximum=255) for key, val in utm.items()}
    link = {
        "image_hash": image_hash,
        "message": text(row["primary_text"], "primary_text", maximum=2000),
        "link": url,
        "call_to_action": {"type": row["cta"], "value": {"link": url}},
    }
    if "headline" in row:
        link["name"] = text(row["headline"], "headline", maximum=100)
    story: dict[str, Any] = {"page_id": identifier(row["page_id"], "page_id"), "link_data": link}
    if row["placements"] in {"instagram", "both"}:
        story["instagram_user_id"] = identifier(
            row.get("instagram_account_id"), "instagram_account_id"
        )
    elif "instagram_account_id" in row:
        raise invalid("instagram_account_id for Facebook-only placement")
    result = {"name": text(row["name"], "name"), "object_story_spec": story}
    if tags:
        result["url_tags"] = urlencode(sorted(tags.items()))
    return result
