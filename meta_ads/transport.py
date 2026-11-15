"""Bounded Graph HTTP; credentials never appear in URLs or diagnostics."""

from __future__ import annotations
import json
import re
from collections.abc import Callable
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request
from flowsteward_extension_sdk import open_pinned_url
from .errors import AdsError, invalid

API_VERSION = "v27.0"
MAX_RESPONSE = 2 * 1024 * 1024
PATH = re.compile(
    r"(?:me|act_[1-9][0-9]{0,31}|[1-9][0-9]{0,31})(?:/(?:adaccounts|permissions|promote_pages|instagram_accounts|adimages|campaigns|adsets|adcreatives|ads))?"
)


def _encoded(params: dict[str, Any]) -> bytes:
    return urlencode(
        {
            k: json.dumps(v, separators=(",", ":")) if isinstance(v, (dict, list, bool)) else str(v)
            for k, v in params.items()
        }
    ).encode()


class GraphClient:
    def __init__(self, token: str, *, opener: Callable[..., Any] = open_pinned_url) -> None:
        self._token = token
        self._opener = opener

    def __repr__(self) -> str:
        return "GraphClient(api_version='v27.0')"

    def request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if (
            method not in {"GET", "POST"}
            or not isinstance(path, str)
            or PATH.fullmatch(path) is None
        ):
            raise invalid("provider path")
        if any(k in {"access_token", "appsecret_proof", "Authorization"} for k in (params or {})):
            raise invalid("provider parameters")
        url = f"https://graph.facebook.com/{API_VERSION}/{path}"
        if params:
            url += "?" + _encoded(params).decode()
        body = _encoded(data or {}) if method == "POST" else None
        headers = {"Authorization": "Bearer " + self._token, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        request = Request(url, data=body, headers=headers, method=method)
        try:
            with self._opener(
                request, timeout_seconds=15.0, purpose="Meta Ads Graph API"
            ) as response:
                payload = self._read(response)
                status = getattr(response, "status", 200)
        except HTTPError as exc:
            try:
                payload = self._read(exc)
            except Exception:
                payload = {}
            self._raise_provider(payload, exc.code, method, exc.headers)
            raise AdsError("provider_unavailable", "Meta rejected the request") from None
        except (URLError, TimeoutError, OSError, ValueError):
            raise AdsError(
                "timeout_unknown" if method == "POST" else "provider_unavailable",
                "The outcome of the Meta request is unknown"
                if method == "POST"
                else "Meta is unavailable",
                ambiguous=method == "POST",
                retryable=method == "GET",
            ) from None
        if not isinstance(payload, dict):
            raise AdsError(
                "timeout_unknown" if method == "POST" else "provider_unavailable",
                "Meta returned an invalid response",
                ambiguous=method == "POST",
                retryable=method == "GET",
            )
        if "error" in payload or status >= 400:
            self._raise_provider(payload, status, method, {})
        return payload

    @staticmethod
    def _read(response: Any) -> Any:
        parts = []
        size = 0
        while True:
            chunk = response.read(16384)
            if not isinstance(chunk, bytes):
                raise ValueError("response type")
            if not chunk:
                break
            size += len(chunk)
            if size > MAX_RESPONSE:
                raise ValueError("response size")
            parts.append(chunk)
        return json.loads(b"".join(parts))

    @staticmethod
    def _raise_provider(payload: Any, status: int, method: str, headers: Any) -> None:
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        raw_code = error.get("code") if isinstance(error, dict) else None
        code = raw_code if type(raw_code) is int else None
        # A timeout, server failure or unstructured response after POST is ambiguous.
        if method == "POST" and (status >= 500 or status == 408 or code is None):
            raise AdsError(
                "timeout_unknown",
                "The outcome of the Meta request is unknown",
                ambiguous=True,
                provider_code=code,
            )
        if code in {102, 190}:
            category, message = "authorization_required", "Reconnect the Meta account"
        elif code in {10, 200, 294}:
            category, message = (
                "permission_denied",
                "Meta permissions are insufficient for this operation",
            )
        elif code in {4, 17, 32, 613, 80000, 80004} or status == 429:
            category, message = "rate_limited", "Meta rate limit reached"
        elif status == 404 or code == 803:
            category, message = "not_found", "The Meta object is unavailable"
        elif status == 400:
            category, message = "invalid_payload", "Meta rejected the advertising parameters"
        else:
            category, message = "provider_unavailable", "Meta could not complete the request"
        retryable = category in {"rate_limited", "provider_unavailable"}
        retry_after = None
        raw_retry = headers.get("Retry-After") if headers else None
        if isinstance(raw_retry, str) and raw_retry.isdigit():
            retry_after = min(int(raw_retry), 300)
        raise AdsError(
            category, message, retryable=retryable, retry_after=retry_after, provider_code=code
        )
