"""Strict host dispatcher, native effect fencing and OAuth verification."""

from __future__ import annotations
from collections.abc import Callable
from typing import Any
from flowsteward_extension_sdk import (
    handshake_result,
    runtime_context_from_payload,
)
from .connection import connection_from_payload, mapping
from .errors import AdsError, invalid
from .images import read_image
from .operations import PARAMETERS, WRITES, execute
from .transport import GraphClient
from .validation import closed, text
from .setup import verify_oauth, bind_account


def handle_request(
    payload: dict[str, Any],
    *,
    client_factory: Callable[..., Any] = GraphClient,
    image_reader: Any = read_image,
) -> dict[str, Any]:
    write_started = False
    try:
        try:
            context = runtime_context_from_payload(payload)
        except ValueError:
            raise AdsError("invalid_host_envelope", "The host envelope is invalid") from None
        if payload.get("mode") == "handshake":
            return {"ok": True, "result": handshake_result()}
        if payload.get("mode") != "action":
            raise invalid("mode")
        action = mapping(payload.get("action"))
        op = action.get("action_id")
        if op == "verify_meta_identity":
            return verify_oauth(payload, client_factory)
        if op == "bind_ad_account":
            return bind_account(payload, client_factory)
        if not isinstance(op, str) or op not in PARAMETERS:
            raise invalid("operation")
        allowed, required = PARAMETERS[op]
        raw = closed(
            action.get("input"), allowed | {"connection_ref"}, required | {"connection_ref"}
        )
        connection_ref = text(raw.pop("connection_ref"), "connection_ref", maximum=128)
        if type(context.raw.get("test_mode", False)) is not bool:
            raise invalid("test_mode")
        connection = connection_from_payload(
            payload, connection_ref, require_verified=op != "list_ad_accounts"
        )
        if op in WRITES and context.raw.get("test_mode") is True:
            return {
                "ok": True,
                "result": {"test_mode_status": "suppressed"},
                "external_effect_status": "suppressed",
                "definitely_no_external_effect": True,
            }
        if op in WRITES and not context.raw.get("external_effect_id"):
            raise AdsError(
                "external_effect_claim_required",
                "The host must claim the external effect before writing to Meta",
            )
        client = client_factory(connection.token)
        # execute can make read calls before a write. Unexpected exceptions are
        # conservatively unknown; classified pre-write validation stays definitive.
        write_started = op in WRITES
        result = execute(client, connection, op, raw, payload, image_reader=image_reader)
        response = {"ok": True, "result": result}
        if op in WRITES:
            response.update(external_effect_status="succeeded", definitely_no_external_effect=False)
        return response
    except AdsError as exc:
        return exc.response()
    except Exception:
        return AdsError(
            "timeout_unknown" if write_started else "internal_error",
            "The outcome of the Meta operation is unknown"
            if write_started
            else "The Meta operation failed",
            ambiguous=write_started,
        ).response()
