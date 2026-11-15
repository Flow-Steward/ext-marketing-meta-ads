from datetime import datetime, UTC
import pytest
from meta_ads.validation import budget_units, campaign_input, ad_set_input, creative_input
from meta_ads.errors import AdsError

NOW = datetime(2030, 1, 1, tzinfo=UTC)


def valid_group():
    return dict(
        campaign_id="200",
        name="Group",
        countries=["LT"],
        age_min=18,
        age_max=65,
        daily_budget="25.00",
        start_time="2030-01-02T10:00:00+02:00",
        end_time="2030-01-05T10:00:00+02:00",
        placements="both",
        dsa_beneficiary="Example",
        dsa_payor="Example",
    )


def test_budget_is_exact_and_currency_aware():
    assert budget_units("25.00", "EUR") == 2500
    assert budget_units("25", "JPY") == 25
    for value in ["0", "-1", "1.001", "NaN", "Infinity", 1.0, True]:
        with pytest.raises(AdsError):
            budget_units(value, "EUR")
    with pytest.raises(AdsError):
        budget_units("25", "UNKNOWN")


def test_campaign_is_ordinary_traffic_only():
    assert campaign_input(dict(name="Launch", ad_category="general")) == dict(
        name="Launch",
        objective="OUTCOME_TRAFFIC",
        special_ad_categories=[],
        status="PAUSED",
        is_adset_budget_sharing_enabled=False,
    )
    for change in [dict(ad_category="housing"), dict(objective="OUTCOME_SALES"), dict(name="")]:
        with pytest.raises(AdsError):
            campaign_input(dict(name="Launch", ad_category="general") | change)


def test_group_pins_profile_and_requires_explicit_dsa():
    body = ad_set_input(valid_group(), "EUR", now=NOW)
    assert body["daily_budget"] == 2500
    assert body["targeting"]["publisher_platforms"] == ["facebook", "instagram"]
    assert body["targeting"]["facebook_positions"] == ["feed"]
    assert body["targeting"]["instagram_positions"] == ["stream"]
    assert body["optimization_goal"] == "LINK_CLICKS"
    assert body["status"] == "PAUSED"
    for change in [
        dict(dsa_payor=""),
        dict(age_min=True),
        dict(age_min=17),
        dict(age_max=66),
        dict(countries=["ZZ"]),
        dict(placements="automatic"),
        dict(start_time="2030-01-02T10:00:00"),
        dict(end_time="2029-01-01T00:00:00Z"),
    ]:
        with pytest.raises(AdsError):
            ad_set_input(valid_group() | change, "EUR", now=NOW)


def test_creative_keeps_url_query_and_refuses_utm_collisions():
    value = dict(
        name="Creative",
        image_hash="a" * 32,
        page_id="300",
        placements="facebook",
        primary_text="Text",
        destination_url="https://example.com/item?lang=lt",
        cta="LEARN_MORE",
        utm={"utm_campaign": "summer sale"},
    )
    body = creative_input(value)
    link = body["object_story_spec"]["link_data"]
    assert "lang=lt" in link["link"]
    assert body["url_tags"] == "utm_campaign=summer+sale"
    for change in [
        dict(destination_url="https://example.com/?utm_campaign=old"),
        dict(placements="instagram"),
        dict(cta="CALL_NOW"),
        dict(destination_url="http://example.com"),
        dict(utm={"access_token": "bad"}),
    ]:
        with pytest.raises(AdsError):
            creative_input(value | change)
