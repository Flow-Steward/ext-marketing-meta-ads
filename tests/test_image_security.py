"""Exercise image parsing through the production boundary without hostile workloads."""

import io
import struct
import zlib

import pytest
from PIL import Image

from meta_ads.errors import AdsError
from meta_ads.images import validate_image


@pytest.mark.parametrize("image_format,mode", [("JPEG", "RGB"), ("PNG", "RGBA"), ("PNG", "P")])
def test_supported_image_bytes_are_preserved(image_format, mode):
    stream = io.BytesIO()
    Image.new(mode, (1080, 1080)).save(stream, image_format)
    original = stream.getvalue()
    assert validate_image(original) is original


@pytest.mark.parametrize("image_format", ["TIFF", "GIF", "BMP", "XBM"])
def test_unsupported_image_never_enters_its_decoder(monkeypatch, image_format):
    Image.init()
    stream = io.BytesIO()
    Image.new("1" if image_format == "XBM" else "RGB", (1080, 1080)).save(stream, image_format)
    _, accept = Image.OPEN[image_format]

    def forbidden_decoder(*args, **kwargs):
        raise AssertionError("An unsupported decoder was entered before rejection")

    monkeypatch.setitem(Image.OPEN, image_format, (forbidden_decoder, accept))
    with pytest.raises(AdsError) as error:
        validate_image(stream.getvalue())
    assert error.value.code == "invalid_payload"


def test_both_image_open_phases_restrict_decoders(monkeypatch):
    stream = io.BytesIO()
    Image.new("RGB", (1080, 1080)).save(stream, "JPEG")
    real_open = Image.open
    phases = []

    def restricted_open(*args, **kwargs):
        formats = kwargs.get("formats")
        assert formats is not None and set(formats) == {"JPEG", "PNG"}
        phases.append(formats)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(Image, "open", restricted_open)
    original = stream.getvalue()
    assert validate_image(original) == original
    assert len(phases) == 2


def test_oversized_apng_is_rejected_before_canvas_allocation(monkeypatch):
    def chunk(kind, data):
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    image = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 22000, 22000, 8, 6, 0, 0, 0))
        + chunk(b"acTL", struct.pack(">II", 1, 0))
        + chunk(b"fcTL", struct.pack(">IIIIIHHBB", 0, 1, 1, 0, 0, 1, 1, 1, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00\x00\x00\x00\x00"))
        + chunk(b"IEND", b"")
    )
    real_fill = Image.core.fill

    def bounded_fill(mode, size, *args, **kwargs):
        if size[0] * size[1] > 1080 * 1080:
            raise AssertionError("Oversized canvas allocation before rejection")
        return real_fill(mode, size, *args, **kwargs)

    monkeypatch.setattr(Image.core, "fill", bounded_fill)
    with pytest.raises(AdsError) as error:
        validate_image(image)
    assert error.value.code == "invalid_payload"


@pytest.mark.parametrize("invalid", [b"", b"not an image", None, b"x" * (5 * 1024 * 1024 + 1)])
def test_invalid_payload_remains_a_domain_error(invalid):
    with pytest.raises(AdsError) as error:
        validate_image(invalid)
    assert error.value.code == "invalid_payload"


def test_wrong_dimensions_remain_rejected():
    stream = io.BytesIO()
    Image.new("RGB", (1080, 1079)).save(stream, "PNG")
    with pytest.raises(AdsError) as error:
        validate_image(stream.getvalue())
    assert error.value.code == "invalid_payload"
