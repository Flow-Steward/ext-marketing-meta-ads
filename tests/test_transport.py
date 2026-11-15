import io
import json
from urllib.error import HTTPError
import pytest
from meta_ads.transport import GraphClient
from meta_ads.errors import AdsError


class Response(io.BytesIO):
    status = 200
    headers: dict[str, str] = {}


def test_token_is_only_a_header_and_api_paths_are_closed():
    seen = []

    def opener(request, **kwargs):
        seen.append(request)
        return Response(b'{"data":[]}')

    client = GraphClient("fixture-not-a-real-token", opener=opener)
    assert client.request("GET", "me/adaccounts", {"limit": 10}) == {"data": []}
    assert seen[0].full_url == "https://graph.facebook.com/v27.0/me/adaccounts?limit=10"
    assert seen[0].get_header("Authorization") == "Bearer fixture-not-a-real-token"
    assert "fixture-not-a-real-token" not in repr(client)
    for path in ["https://evil.test", "../me", "me?access_token=bad", "me/../../1", "//evil.test"]:
        with pytest.raises(AdsError):
            client.request("GET", path)
    assert len(seen) == 1


@pytest.mark.parametrize("body", [b"broken", b"[]", b'{"id":null}'])
def test_mutation_malformed_response_is_not_a_safe_retry(body):
    client = GraphClient("fixture", opener=lambda *a, **k: Response(body))
    if body == b'{"id":null}':
        assert client.request("POST", "act_100/campaigns", data={"name": "test"}) == {"id": None}
    else:
        with pytest.raises(AdsError) as exc:
            client.request("POST", "act_100/campaigns", data={"name": "test"})
        assert exc.value.ambiguous
        assert not exc.value.retryable


def test_write_timeout_and_5xx_are_unknown_but_explicit_provider_rejection_is_safe():
    def failure(*a, **k):
        raise TimeoutError("fixture-not-a-real-token")

    client = GraphClient("fixture-not-a-real-token", opener=failure)
    with pytest.raises(AdsError) as exc:
        client.request("POST", "act_100/ads", data={})
    assert exc.value.code == "timeout_unknown"
    assert "fixture-not-a-real-token" not in str(exc.value)

    def rejected(*a, **k):
        raise HTTPError(
            "https://graph.facebook.com",
            400,
            "error",
            {},
            io.BytesIO(
                json.dumps({"error": {"code": 190, "message": "fixture-not-a-real-token"}}).encode()
            ),
        )

    with pytest.raises(AdsError) as exc:
        GraphClient("fixture-not-a-real-token", opener=rejected).request(
            "POST", "act_100/ads", data={}
        )
    assert exc.value.code == "authorization_required"
    assert not exc.value.ambiguous
    assert "fixture-not-a-real-token" not in str(exc.value)


@pytest.mark.parametrize("method", ["POST", "GET"])
def test_structured_request_timeout_preserves_uncertain_write_outcome(method):
    def timeout_response(*args, **kwargs):
        raise HTTPError(
            "https://graph.facebook.com",
            408,
            "timeout",
            {},
            io.BytesIO(b'{"error":{"code":1,"message":"fixture"}}'),
        )

    with pytest.raises(AdsError) as exc:
        GraphClient("fixture", opener=timeout_response).request(method, "act_100/ads", data={})
    assert exc.value.ambiguous is (method == "POST")
    assert exc.value.code == ("timeout_unknown" if method == "POST" else "provider_unavailable")
    assert exc.value.retryable is (method == "GET")
