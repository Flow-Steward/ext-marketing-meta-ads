"""Safe errors and native external-effect facts."""

from __future__ import annotations
from typing import Any


class AdsError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        ambiguous: bool = False,
        retryable: bool = False,
        retry_after: int | None = None,
        provider_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.code, self.message = code, message
        self.ambiguous, self.retryable = ambiguous, retryable
        self.retry_after, self.provider_code = retry_after, provider_code

    def response(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "ok": False,
            "result": {},
            "error_code": self.code,
            "error": self.message,
            "errors": [{"code": self.code, "message": self.message}],
            "retryable": self.retryable,
            "definitely_no_external_effect": not self.ambiguous,
            "external_effect_status": "timeout_unknown" if self.ambiguous else "failed",
        }
        if self.retry_after is not None:
            result["retry_after_seconds"] = self.retry_after
        if self.provider_code is not None:
            result["provider_error_code"] = self.provider_code
        return result


def invalid(field: str) -> AdsError:
    return AdsError("invalid_payload", f"Invalid or missing {field}")
