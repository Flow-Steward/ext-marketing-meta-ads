"""Read host-granted artifacts and inspect images without rewriting the bytes."""

from __future__ import annotations
import io
from typing import Any
from PIL import Image, UnidentifiedImageError
from flowsteward_extension_sdk import stream_artifact_bytes
from .errors import AdsError

MAX_IMAGE_BYTES = 5 * 1024 * 1024


def validate_image(data: bytes) -> bytes:
    if not isinstance(data, bytes) or not data or len(data) > MAX_IMAGE_BYTES:
        raise AdsError("invalid_payload", "Image must be at most 5 MiB")
    try:
        with Image.open(io.BytesIO(data)) as image:
            if (
                image.format not in {"JPEG", "PNG"}
                or image.size != (1080, 1080)
                or getattr(image, "n_frames", 1) != 1
            ):
                raise AdsError("invalid_payload", "Use a single 1080 x 1080 JPEG or PNG image")
            image.verify()
        with Image.open(io.BytesIO(data)) as image:
            image.load()
    except AdsError:
        raise
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
        raise AdsError("invalid_payload", "The image cannot be decoded") from exc
    return data


def read_image(payload: dict[str, Any], artifact: str) -> bytes:
    chunks = []
    size = 0
    try:
        for chunk in stream_artifact_bytes(
            payload, artifact_id=artifact, binding_key="meta_image", timeout_seconds=15.0
        ):
            size += len(chunk)
            if size > MAX_IMAGE_BYTES:
                raise AdsError("invalid_payload", "Image exceeds 5 MiB")
            chunks.append(chunk)
    except AdsError:
        raise
    except Exception:
        raise AdsError(
            "artifact_unavailable", "The approved image artifact is unavailable"
        ) from None
    return validate_image(b"".join(chunks))
