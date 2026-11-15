import copy
import pytest
from test_operations import FakeGraph, payload, run
from meta_ads.runtime import handle_request
from meta_ads.validation import creative_input
from meta_ads.errors import AdsError


def setup_payload(action_id, inputs=None, config=None):
    request = payload(action_id, inputs or {}, effect=False)
    conn = request["action"]["target"]["connection"]
    conn["config"] = config if config is not None else {"oauth": {"lifecycle_status": "connected"}}
    request["runtime_context"]["resources"] = {
        "project_connection": {
            "connection_id": "conn-1",
            "connection_config": copy.deepcopy(conn["config"]),
        }
    }
    return request


def test_oauth_post_connect_supports_real_host_target_without_account_configuration():
    request = setup_payload("verify_meta_identity")
    request["action"]["input"] = {}
    request["action"]["target"]["connection"] = {
        "connection_id": "conn-1",
        "secrets": {"access_token": "fixture-not-a-real-token"},
    }
    result = handle_request(request, client_factory=lambda token: FakeGraph())
    assert result["ok"] is True, result
    assert result["result"]["subject"] == "900"


def test_binding_account_is_a_separate_native_host_mutation_and_is_immutable():
    graph = FakeGraph()
    request = setup_payload("bind_ad_account", {"ad_account_id": "100"})
    result = handle_request(request, client_factory=lambda token: graph)
    assert result["ok"] is True, result
    mutation = result["host_mutations"][0]
    assert mutation["kind"] == "project_connection_config_compare_and_set"
    assert mutation["payload"] == {
        "connection_id": "conn-1",
        "expect": [{"path": "bound_ad_account_id", "state": "absent_or_equals", "value": "100"}],
        "set": {"ad_account_id": "100", "bound_ad_account_id": "100"},
    }
    config = {
        "ad_account_id": "100",
        "bound_ad_account_id": "100",
        "oauth": {"lifecycle_status": "connected"},
    }
    assert not any(c[0] == "POST" for c in graph.calls)
    request = setup_payload("bind_ad_account", {"ad_account_id": "999"}, config)
    result = handle_request(request, client_factory=lambda token: pytest.fail("provider called"))
    assert result["error_code"] == "invalid_connection"


def test_changing_only_configured_account_is_refused_before_any_provider_call():
    request = payload("create_campaign", {"name": "Launch", "ad_category": "general"})
    request["action"]["target"]["connection"]["config"]["ad_account_id"] = "999"
    result = handle_request(request, client_factory=lambda token: pytest.fail("provider called"))
    assert result["error_code"] == "invalid_connection"


def test_malformed_nested_values_are_validation_errors_not_unknown_mutations():
    value = dict(
        name="Creative",
        image_hash="a" * 32,
        page_id="300",
        placements="facebook",
        primary_text="Text",
        destination_url="https://example.com",
        cta=["LEARN_MORE"],
    )
    with pytest.raises(AdsError) as exc:
        creative_input(value)
    assert exc.value.code == "invalid_payload"


@pytest.mark.parametrize("placements", ["facebook", "instagram", "both"])
def test_complete_paused_creation_chain_for_each_supported_placement(placements):
    graph = FakeGraph()
    result = run(graph, "create_campaign", dict(name="Launch", ad_category="general"))
    assert result["ok"], result
    result = run(
        graph,
        "create_ad_set",
        dict(
            campaign_id=result["result"]["campaign_id"],
            name="Group",
            countries=["LT"],
            age_min=18,
            age_max=65,
            daily_budget="25.00",
            start_time="2099-01-02T00:00:00Z",
            end_time="2099-01-03T00:00:00Z",
            placements=placements,
            dsa_beneficiary="Example",
            dsa_payor="Example",
        ),
    )
    assert result["ok"], result
    post = next(c for c in reversed(graph.calls) if c[0] == "POST")
    graph.adset["targeting"] = copy.deepcopy(post[3]["targeting"])
    inputs = dict(
        name="Creative",
        image_hash="a" * 32,
        page_id="300",
        placements=placements,
        primary_text="Text",
        destination_url="https://example.com/",
        cta="LEARN_MORE",
    )
    if placements != "facebook":
        inputs["instagram_account_id"] = "301"
    result = run(graph, "create_creative", inputs)
    assert result["ok"], result
    post = next(c for c in reversed(graph.calls) if c[0] == "POST")
    graph.creative["object_story_spec"] = copy.deepcopy(post[3]["object_story_spec"])
    result = run(graph, "create_ad", dict(name="Ad", ad_set_id="400", creative_id="500"))
    assert result["ok"], result
    assert result["result"]["ad_id"] == "600"
    assert all(
        c[3]["status"] == "PAUSED"
        for c in graph.calls
        if c[0] == "POST" and c[1].split("/")[-1] in {"campaigns", "adsets", "ads"}
    )


def test_confirmed_status_write_retains_success_when_followup_read_fails():
    graph = FakeGraph()
    original = graph.request
    did_write = False

    def request(method, path, params=None, *, data=None):
        nonlocal did_write
        if did_write and method == "GET":
            raise AdsError("provider_unavailable", "Meta unavailable", retryable=True)
        result = original(method, path, params, data=data)
        if method == "POST":
            did_write = True
        return result

    graph.request = request
    result = run(
        graph, "set_delivery_status", dict(object_type="ad", object_id="600", status="ACTIVE")
    )
    assert result["ok"], result
    assert result["result"]["object_id"] == "600"
    assert result["result"]["effective_status"] is None
    assert result["external_effect_status"] == "succeeded"


@pytest.mark.parametrize("anchor", [None, "999", [], {}])
def test_explicit_nonmatching_account_anchor_is_rejected_before_provider_access(anchor):
    request = setup_payload(
        "bind_ad_account",
        {"ad_account_id": "100"},
        {"bound_ad_account_id": anchor, "oauth": {"lifecycle_status": "connected"}},
    )
    result = handle_request(request, client_factory=lambda token: pytest.fail("provider called"))
    assert result["error_code"] == "invalid_connection"
    assert "host_mutations" not in result


def test_binding_same_account_replays_conditional_patch_without_oauth_snapshot():
    request = setup_payload(
        "bind_ad_account",
        {"ad_account_id": "100"},
        {
            "ad_account_id": "100",
            "bound_ad_account_id": "100",
            "oauth": {"lifecycle_status": "connected", "subject": "900"},
        },
    )
    result = handle_request(request, client_factory=lambda token: FakeGraph())
    assert result["ok"], result
    mutation = result["host_mutations"][0]
    assert mutation["kind"] == "project_connection_config_compare_and_set"
    assert mutation["payload"]["set"] == {"ad_account_id": "100", "bound_ad_account_id": "100"}
    assert mutation["payload"]["expect"] == [
        {"path": "bound_ad_account_id", "state": "absent_or_equals", "value": "100"}
    ]
    assert "oauth" not in mutation["payload"]["set"]


def test_binding_in_test_mode_has_no_host_mutation_or_provider_access():
    request = setup_payload("bind_ad_account", {"ad_account_id": "100"})
    request["runtime_context"]["test_mode"] = True
    result = handle_request(request, client_factory=lambda token: pytest.fail("provider called"))
    assert result["ok"], result
    assert result["result"]["test_mode_status"] == "suppressed"
    assert "host_mutations" not in result
