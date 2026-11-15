import copy
import io
import pytest
from PIL import Image
from meta_ads.runtime import handle_request


def payload(op, inputs=None, *, effect=True):
    return {
        "contract_version": "extension_host_v2",
        "mode": "action",
        "action": {
            "action_id": op,
            "input": {"connection_ref": "conn-1", **(inputs or {})},
            "target": {
                "connection": {
                    "connection_id": "conn-1",
                    "connection_type": "meta_ads_account",
                    "config": {
                        "ad_account_id": "100",
                        "bound_ad_account_id": "100",
                        "oauth": {
                            "lifecycle_status": "connected",
                            "safe_metadata": {"ad_account_id": "100"},
                        },
                    },
                    "secrets": {"access_token": "fixture-not-a-real-token"},
                }
            },
        },
        "runtime_context": {
            "group_id": None,
            "for_each_id": None,
            "step_id": "step-1",
            "step_execution_id": "exec-1",
            "job_id": "job-1",
            "test_mode": False,
            **({"external_effect_id": "effect-1"} if effect else {}),
        },
    }


class FakeGraph:
    def __init__(self):
        self.calls = []
        self.campaign = {
            "id": "200",
            "account_id": "100",
            "objective": "OUTCOME_TRAFFIC",
            "special_ad_categories": [],
            "status": "PAUSED",
            "is_adset_budget_sharing_enabled": False,
        }
        self.adset = {
            "id": "400",
            "account_id": "100",
            "campaign_id": "200",
            "status": "PAUSED",
            "daily_budget": "2500",
            "lifetime_budget": "0",
            "optimization_goal": "LINK_CLICKS",
            "billing_event": "IMPRESSIONS",
            "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
            "destination_type": "WEBSITE",
            "targeting": {
                "geo_locations": {"countries": ["LT"]},
                "age_min": 18,
                "age_max": 65,
                "publisher_platforms": ["facebook"],
                "facebook_positions": ["feed"],
            },
            "dsa_beneficiary": "Example",
            "dsa_payor": "Example",
        }
        self.creative = {
            "id": "500",
            "account_id": "100",
            "object_story_spec": {
                "page_id": "300",
                "link_data": {
                    "link": "https://example.com/",
                    "image_hash": "a" * 32,
                    "message": "Text",
                    "call_to_action": {
                        "type": "LEARN_MORE",
                        "value": {"link": "https://example.com/"},
                    },
                },
            },
        }

    def request(self, method, path, params=None, *, data=None):
        self.calls.append((method, path, params, data))
        if method == "POST":
            return (
                {"success": True}
                if path.isdigit()
                else {
                    "id": {
                        "campaigns": "200",
                        "adsets": "400",
                        "adcreatives": "500",
                        "ads": "600",
                    }.get(path.split("/")[-1], "700")
                }
            )
        if path == "act_100":
            return {
                "id": "act_100",
                "account_id": "100",
                "currency": "EUR",
                "timezone_name": "Europe/Vilnius",
                "account_status": 1,
            }
        if path == "200":
            return copy.deepcopy(self.campaign)
        if path == "400":
            return copy.deepcopy(self.adset)
        if path == "500":
            return copy.deepcopy(self.creative)
        if path == "600":
            return {
                "id": "600",
                "account_id": "100",
                "adset_id": "400",
                "creative": {"id": "500"},
                "campaign_id": "200",
                "status": "PAUSED",
                "effective_status": "CAMPAIGN_PAUSED",
            }
        if path == "me":
            return {"id": "900", "name": "Example user"}
        if path == "me/permissions":
            return {
                "data": [
                    {"permission": s, "status": "granted"}
                    for s in [
                        "ads_management",
                        "pages_show_list",
                        "pages_read_engagement",
                        "pages_manage_ads",
                        "instagram_basic",
                    ]
                ]
            }
        if path == "act_100/promote_pages":
            return {
                "data": [
                    {
                        "id": "300",
                        "name": "Example",
                        "instagram_business_account": {"id": "301", "username": "example"},
                    }
                ]
            }
        if path == "act_100/instagram_accounts":
            return {"data": [{"id": "301", "username": "example"}]}
        if path == "act_100/adimages":
            return {"data": [{"hash": "a" * 32, "width": 1080, "height": 1080}]}
        raise AssertionError((method, path, params, data))


def run(graph, op, inputs=None, **kwargs):
    return handle_request(payload(op, inputs), client_factory=lambda token: graph, **kwargs)


def test_creation_is_paused_and_write_requires_host_claim():
    graph = FakeGraph()
    result = run(graph, "create_campaign", dict(name="Launch", ad_category="general"))
    assert result["ok"] is True, result
    assert result["result"]["campaign_id"] == "200"
    assert result["external_effect_status"] == "succeeded"
    assert next(c for c in graph.calls if c[0] == "POST")[3]["status"] == "PAUSED"
    graph.calls.clear()
    result = handle_request(
        payload("create_campaign", dict(name="Launch", ad_category="general"), effect=False),
        client_factory=lambda token: graph,
    )
    assert result["error_code"] == "external_effect_claim_required"
    assert not graph.calls


def test_test_mode_never_calls_provider_and_never_invents_ids():
    request = payload("create_campaign", dict(name="Launch", ad_category="general"))
    request["runtime_context"]["test_mode"] = True
    result = handle_request(request, client_factory=lambda token: pytest.fail("provider called"))
    assert result["result"] == {"test_mode_status": "suppressed"}
    assert result["external_effect_status"] == "suppressed"


def test_account_scope_cannot_be_overridden():
    graph = FakeGraph()
    request = payload(
        "create_campaign", dict(name="Launch", ad_category="general", ad_account_id="999")
    )
    result = handle_request(request, client_factory=lambda token: graph)
    assert result["error_code"] == "invalid_payload"
    assert not graph.calls
    request = payload("get_object_status", dict(object_type="campaign", object_id="200"))
    request["action"]["input"]["connection_ref"] = "foreign"
    result = handle_request(request, client_factory=lambda token: graph)
    assert result["error_code"] == "invalid_connection"
    assert not graph.calls


def test_existing_campaign_must_be_compatible_but_need_not_be_extension_created():
    graph = FakeGraph()
    graph.campaign["objective"] = "OUTCOME_SALES"
    inputs = dict(
        campaign_id="200",
        name="Group",
        countries=["LT"],
        age_min=18,
        age_max=65,
        daily_budget="25.00",
        start_time="2099-01-02T00:00:00Z",
        end_time="2099-01-03T00:00:00Z",
        placements="facebook",
        dsa_beneficiary="Example",
        dsa_payor="Example",
    )
    result = run(graph, "create_ad_set", inputs)
    assert result["error_code"] == "unsupported_configuration"
    assert not any(c[0] == "POST" for c in graph.calls)
    graph.campaign["objective"] = "OUTCOME_TRAFFIC"
    assert run(graph, "create_ad_set", inputs)["ok"] is True


def test_activate_ad_does_not_activate_paused_parents():
    graph = FakeGraph()
    result = run(
        graph, "set_delivery_status", dict(object_type="ad", object_id="600", status="ACTIVE")
    )
    assert result["ok"] is True, result
    posts = [c for c in graph.calls if c[0] == "POST"]
    assert [(c[1], c[3]) for c in posts] == [("600", {"status": "ACTIVE"})]
    assert result["result"]["effective_status"] == "CAMPAIGN_PAUSED"


def test_foreign_object_and_missing_write_id_never_become_success():
    graph = FakeGraph()
    graph.campaign["account_id"] = "999"
    result = run(
        graph, "set_delivery_status", dict(object_type="campaign", object_id="200", status="ACTIVE")
    )
    assert result["error_code"] == "invalid_connection"
    assert not any(c[0] == "POST" for c in graph.calls)
    graph = FakeGraph()
    original = graph.request

    def malformed(method, path, params=None, *, data=None):
        return {} if method == "POST" else original(method, path, params, data=data)

    graph.request = malformed
    result = run(graph, "create_campaign", dict(name="Launch", ad_category="general"))
    assert result["error_code"] == "timeout_unknown"
    assert result["external_effect_status"] == "timeout_unknown"
    assert result["definitely_no_external_effect"] is False


def test_image_validation_reads_only_granted_artifact_and_does_not_reencode():
    from meta_ads.images import validate_image

    stream = io.BytesIO()
    Image.new("RGB", (1080, 1080), "white").save(stream, format="PNG")
    raw = stream.getvalue()
    assert validate_image(raw) == raw
    for bad in [b"not an image", raw[:40]]:
        with pytest.raises(Exception):
            validate_image(bad)
    stream = io.BytesIO()
    Image.new("RGB", (100, 100)).save(stream, format="JPEG")
    with pytest.raises(Exception):
        validate_image(stream.getvalue())
