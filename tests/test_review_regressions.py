import json
from pathlib import Path
import subprocess
import sys
import pytest
import yaml
from flowsteward_extension_sdk import find_artifact_descriptor
from meta_ads import images
from meta_ads.errors import AdsError
from test_operations import FakeGraph, run

ROOT = Path(__file__).resolve().parents[1]


def test_native_host_validator_accepts_oauth_config_when_binding():
    # This is a test-only adapter to the owning application's real validator.
    # Production extension modules never import private Core code.
    code = """import json,sys,yaml
from core.application.extensions.connection_partition_validation import validate_connection_partition
types=yaml.safe_load(open(sys.argv[1]))['connection_types']
extension={'runtime':{'extension_contract_v2':{'connection_types':types}}}
errors=validate_connection_partition(extension,connection_type='meta_ads_account',partition='config',value={'ad_account_id':'100','bound_ad_account_id':'100','oauth':{'lifecycle_status':'connected','subject':'900','safe_metadata':{}}})
print(json.dumps(errors))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / "contracts/connection_types.yaml")],
        cwd="/app",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == []


def test_requested_artifact_is_not_replaced_by_another_matching_binding(monkeypatch):
    payload = {
        "artifacts": {
            "inputs": [
                {"artifact_id": "b", "binding_key": "meta_image"},
                {"artifact_id": "a", "binding_key": "meta_image"},
            ]
        }
    }

    def stream(payload, **kwargs):
        kwargs.pop("timeout_seconds", None)
        descriptor = find_artifact_descriptor(payload, **kwargs)
        yield descriptor["artifact_id"].encode()

    monkeypatch.setattr(images, "stream_artifact_bytes", stream)
    monkeypatch.setattr(images, "validate_image", lambda value: value)
    assert images.read_image(payload, "artifact:a") == b"a"
    with pytest.raises(AdsError):
        images.read_image(payload, "artifact:missing")


def test_opted_out_creative_features_remain_compatible():
    graph = FakeGraph()
    graph.creative["degrees_of_freedom_spec"] = {
        "creative_features_spec": {"standard_enhancements": {"enroll_status": "OPT_OUT"}}
    }
    result = run(graph, "create_ad", dict(name="Ad", ad_set_id="400", creative_id="500"))
    assert result["ok"], result
    graph.creative["degrees_of_freedom_spec"]["creative_features_spec"]["standard_enhancements"][
        "enroll_status"
    ] = "OPT_IN"
    before = len([c for c in graph.calls if c[0] == "POST"])
    result = run(graph, "create_ad", dict(name="Ad", ad_set_id="400", creative_id="500"))
    assert result["error_code"] == "unsupported_configuration"
    assert len([c for c in graph.calls if c[0] == "POST"]) == before


@pytest.mark.parametrize("file", ["new_campaign.yaml", "existing_group.yaml"])
def test_facebook_example_has_a_creative_path_without_instagram_selector(file):
    data = yaml.safe_load((ROOT / "examples" / file).read_text())
    creative_steps = [
        s
        for s in data["steps"]
        if s.get("override", {}).get("connector", {}).get("action_id") == "create_creative"
    ]
    assert any("instagram_account_id" not in s["input_mapping_jsonb"] for s in creative_steps)
    assert any("instagram_account_id" in s["input_mapping_jsonb"] for s in creative_steps)
    decisions = [
        s
        for s in data["steps"]
        if s.get("step_kind") == "if"
        and "placements" in s.get("override", {}).get("if", {}).get("condition", "")
    ]
    assert decisions


@pytest.mark.parametrize(
    "requested,binding", [("artifact:missing", "meta_image"), ("artifact:a", "other_image")]
)
def test_missing_id_or_wrong_image_binding_cannot_fall_back(monkeypatch, requested, binding):
    payload = {
        "artifacts": {
            "inputs": [
                {"artifact_id": "a", "binding_key": binding},
                {"artifact_id": "b", "binding_key": "meta_image"},
            ]
        }
    }

    def stream(payload, **kwargs):
        kwargs.pop("timeout_seconds", None)
        yield find_artifact_descriptor(payload, **kwargs)["artifact_id"].encode()

    monkeypatch.setattr(images, "stream_artifact_bytes", stream)
    monkeypatch.setattr(images, "validate_image", lambda value: value)
    with pytest.raises(AdsError) as exc:
        images.read_image(payload, requested)
    assert exc.value.code == "artifact_unavailable"


@pytest.mark.parametrize(
    "spec",
    [
        {
            "creative_features_spec": {
                "a": {"enroll_status": "OPT_OUT"},
                "b": {"enroll_status": "OPT_IN"},
            }
        },
        {"creative_features_spec": {"a": {}}},
        {"creative_features_spec": {"a": {"enroll_status": "OPT_OUT", "unknown_transform": True}}},
        {"creative_features_spec": []},
        {"creative_features_spec": {"a": "OPT_OUT"}},
        {"text_transformation_types": ["ALL"]},
        ["OPT_OUT"],
    ],
)
def test_enabled_or_unrecognized_creative_transformations_remain_rejected(spec):
    graph = FakeGraph()
    graph.creative["degrees_of_freedom_spec"] = spec
    result = run(graph, "create_ad", dict(name="Ad", ad_set_id="400", creative_id="500"))
    assert result["error_code"] == "unsupported_configuration"
    assert not any(call[0] == "POST" for call in graph.calls)


@pytest.mark.parametrize("file", ["new_campaign.yaml", "existing_group.yaml"])
@pytest.mark.parametrize("placements", ["facebook", "instagram", "both"])
def test_example_routes_one_valid_creative_then_one_ad(file, placements):
    # Exercise the actual host's condition evaluator and mapping renderer.
    # This adapter is test-only; the extension has no private Core import.
    inputs = {
        "ad_name": "Example",
        "placements": placements,
        "page_id": "300",
        "primary_text": "Example",
        "destination_url": "https://example.com/",
    }
    if placements != "facebook":
        inputs["instagram_account_id"] = "301"
    code = """import json,sys,yaml
from core.application.orchestration.control_flow_step_helpers import _evaluate_condition
from core.application.orchestration.connector_mapping import _resolve_mapping_value
workflow=yaml.safe_load(open(sys.argv[1])); inputs=json.loads(sys.argv[2])
state={'inputs': inputs, 'image_hash': 'a'*32}
steps={s['step_id']: s for s in workflow['steps']}
edges=workflow['settings']['execution_graph']['edges']
current='upload_image'; creatives=[]; visited=[]
for _ in range(10):
    step=steps[current]; visited.append(current)
    if step.get('override',{}).get('connector',{}).get('action_id')=='create_ad':
        break
    if step.get('override',{}).get('connector',{}).get('action_id')=='create_creative':
        mapping=step['input_mapping_jsonb']
        creatives.append({k:_resolve_mapping_value(v,state) for k,v in mapping.items()})
    outcome='success'
    if step.get('step_kind')=='if':
        outcome='true' if _evaluate_condition(step['override']['if']['condition'],state) else 'false'
    targets=[e['target']['id'] for e in edges if e['source']['id']==current and e['outcome']['name']==outcome]
    if len(targets)!=1: raise RuntimeError('ambiguous or missing workflow route')
    current=targets[0]
print(json.dumps({'creatives':creatives,'last':current,'visited':visited}))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / "examples" / file), json.dumps(inputs)],
        cwd="/app",
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    route = json.loads(result.stdout)
    assert route["last"] == "create_ad"
    assert len(route["creatives"]) == 1
    values = route["creatives"][0]
    from meta_ads.validation import creative_input

    assert creative_input(values)["placements"] == placements
    assert ("instagram_account_id" in values) == (placements != "facebook")
