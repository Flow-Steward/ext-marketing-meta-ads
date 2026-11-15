"""Bounded JSON subprocess entrypoint."""

from __future__ import annotations
import json
import sys
from flowsteward_extension_sdk import handshake_result, runtime_context_from_payload


def main() -> int:
    raw = sys.stdin.buffer.read(1024 * 1024 + 1)
    try:
        if len(raw) > 1024 * 1024:
            raise ValueError("request size")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("request shape")
        runtime_context_from_payload(payload)
        if payload.get("mode") == "handshake":
            result = {"ok": True, "result": handshake_result()}
        else:
            from meta_ads.runtime import handle_request

            result = handle_request(payload)
    except (ValueError, UnicodeDecodeError):
        result = {
            "ok": False,
            "result": {},
            "error_code": "invalid_host_envelope",
            "error": "Invalid host envelope",
            "errors": [{"code": "invalid_host_envelope", "message": "Invalid host envelope"}],
        }
    except Exception:
        result = {
            "ok": False,
            "result": {},
            "error_code": "internal_error",
            "error": "The extension failed",
            "errors": [{"code": "internal_error", "message": "The extension failed"}],
            "external_effect_status": "timeout_unknown",
            "definitely_no_external_effect": False,
        }
    serialized = json.dumps(result, ensure_ascii=False)
    if len(serialized.encode()) > 2 * 1024 * 1024:
        serialized = json.dumps(
            {
                "ok": False,
                "result": {},
                "error_code": "timeout_unknown",
                "error": "The response exceeds the output limit",
                "errors": [
                    {"code": "timeout_unknown", "message": "The response exceeds the output limit"}
                ],
                "external_effect_status": "timeout_unknown",
                "definitely_no_external_effect": False,
            }
        )
        result["ok"] = False
    print(serialized)
    return 0 if result.get("ok") is True else 2


if __name__ == "__main__":
    raise SystemExit(main())
