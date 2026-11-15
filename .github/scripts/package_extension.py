#!/usr/bin/env python3
"""Package this repository as a Flow Steward extension archive.

    python3 .github/scripts/package_extension.py   ->  dist/<extension_id>-<version>.zip
                                                       or, with per-architecture wheels,
                                                       dist/<extension_id>-<version>-linux-amd64.zip
                                                       dist/<extension_id>-<version>-linux-arm64.zip

The archive is what ``flow-steward extensions bundle`` produces: every tracked
file under one top folder named after the id (``.`` and ``-`` become ``_``),
without the repository's own tooling (.github/) and without development caches.
When wheels/ holds wheels built for one architecture, it is what
``bundle --runtime-target`` produces instead: one archive per target, each with
only the wheels that install on it and a .fs-package.yaml naming the target.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import stat
from email.parser import BytesParser
import struct
import subprocess
import sys
import zipfile
from pathlib import Path, PurePosixPath

import yaml

ROOT = Path(__file__).resolve().parents[2]
# The product bundler leaves these out (core/application/tooling/extensions/bundle_extension.py).
EXCLUDED_DIRECTORIES = {
    ".fs-python",
    ".hypothesis",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "dev-wheels",
}
EXCLUDED_FILES = {".fs-extension-install.yaml", ".fs-package.yaml"}
REPOSITORY_TOOLING = {
    ".github",
    "dist",
    "tests",
    "scripts",
    "docs",
    ".gitignore",
    "pyproject.toml",
    "requirements-dev.txt",
    "requirements.in",
}
TARGETS = {"linux-amd64": "x86_64", "linux-arm64": "aarch64"}
MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNPACKED_BYTES = 150 * 1024 * 1024
# The logo Flow Steward shows: icon.png next to extension.yaml (the catalog's and the
# installer's rule; replace the file with your own logo).
ICON_FILE = "icon.png"
MAX_ICON_BYTES = 100 * 1024
MIN_ICON_PX, MAX_ICON_PX = 32, 512


def manifest_field(name: str) -> str:
    text = (ROOT / "extension.yaml").read_text(encoding="utf-8")
    match = re.search(rf"^{name}:\s*['\"]?([^'\"\s#]+)", text, re.M)
    if not match:
        sys.exit(f"extension.yaml has no {name}")
    return match.group(1)


def shipped_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    files = []
    for name in sorted(p for p in out.split("\0") if p):
        parts = PurePosixPath(name).parts
        if parts[0] in REPOSITORY_TOOLING or name in REPOSITORY_TOOLING:
            continue
        if any(part in EXCLUDED_DIRECTORIES for part in parts) or parts[-1] in EXCLUDED_FILES:
            continue
        if parts[0] == "bin":
            sys.exit("bin/ needs one archive per runtime target; see the catalog README")
        files.append(name)
    return files


def icon_problem(data: bytes | None) -> str:
    """Why icon.png cannot be the extension's logo, or "" when it can."""
    if data is None:
        return f"add {ICON_FILE} (the extension's logo) next to extension.yaml and commit it"
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n" or data[12:16] != b"IHDR":
        return f"{ICON_FILE} is not a PNG"
    width, height = struct.unpack(">II", data[16:24])
    if width != height or not MIN_ICON_PX <= width <= MAX_ICON_PX:
        return f"{ICON_FILE} must be square, {MIN_ICON_PX}-{MAX_ICON_PX} px (is {width}x{height})"
    if len(data) > MAX_ICON_BYTES:
        return f"{ICON_FILE} is larger than {MAX_ICON_BYTES} bytes"
    return ""


def _wheel_platforms(name: str) -> list[str]:
    return name[: -len(".whl")].rsplit("-", 1)[-1].split(".") if name.endswith(".whl") else ["any"]


def wheel_supports_target(name: str, target: str) -> bool:
    """Flow Steward's rule: a pure wheel, or a Linux wheel for this architecture."""
    arch = TARGETS[target]
    return any(
        platform == "any"
        or (
            platform.startswith(("manylinux", "musllinux", "linux"))
            and platform.endswith(f"_{arch}")
        )
        for platform in _wheel_platforms(PurePosixPath(name).name)
    )


def _check_locked_wheels(files: list[str]) -> None:
    manifest = yaml.safe_load((ROOT / "extension.yaml").read_text())
    requirements = manifest.get("python_requirements")
    if not isinstance(requirements, list) or not requirements:
        sys.exit("A native python_requirements lock is required")
    locks = {}
    for row in requirements:
        key = (re.sub(r"[-_.]+", "-", row["name"]).lower(), row["version"])
        locks[key] = set(row["hashes"])
    found = set()
    for name in files:
        if not name.startswith("wheels/") or not name.endswith(".whl"):
            continue
        data = (ROOT / name).read_bytes()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        with zipfile.ZipFile(ROOT / name) as wheel:
            metadata = [n for n in wheel.namelist() if n.endswith(".dist-info/METADATA")]
            if len(metadata) != 1:
                sys.exit("Wheel must contain exactly one distribution metadata record")
            row = BytesParser().parsebytes(wheel.read(metadata[0]))
        key = (re.sub(r"[-_.]+", "-", str(row["Name"])).lower(), str(row["Version"]))
        if key not in locks or digest not in locks[key]:
            sys.exit("Wheel SHA256/name/version differs from python_requirements lock: " + name)
        found.add(key)
    if found != set(locks):
        sys.exit("Missing compatible wheel for a locked runtime requirement")


def _write(out: Path, folder: str, files: list[str], *, target: str) -> None:
    unpacked = 0
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        contents = []
        for name in files:
            path = ROOT / name
            if not path.is_file() or path.is_symlink():
                sys.exit("Unsafe package input: " + name)
            contents.append((name, path.read_bytes()))
        contents.append((".fs-package.yaml", f"runtime_target: {target}\n".encode()))
        for name, data in contents:
            info = zipfile.ZipInfo(f"{folder}/{name}", (2020, 1, 1, 0, 0, 0))
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, data, compresslevel=9)
            unpacked += len(data)
    size = out.stat().st_size
    if size > MAX_ZIP_BYTES or unpacked > MAX_UNPACKED_BYTES:
        sys.exit(f"{out.name}: installer archive-size limit exceeded")
    print(f"{out} {size} zipped / {unpacked} unpacked bytes")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime-target", required=True, choices=sorted(TARGETS))
    parser.add_argument("--out-dir", required=True, type=Path)
    args = parser.parse_args()
    extension_id, version = manifest_field("extension_id"), manifest_field("version")
    folder = extension_id.replace(".", "_").replace("-", "_")
    files = shipped_files()
    problem = icon_problem((ROOT / ICON_FILE).read_bytes() if ICON_FILE in files else None)
    if problem:
        sys.exit(f"{extension_id} {version}: {problem}")
    files = [
        name
        for name in files
        if not name.endswith(".whl") or wheel_supports_target(name, args.runtime_target)
    ]
    _check_locked_wheels(files)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    destination = args.out_dir / f"{extension_id}-{version}-{args.runtime_target}.zip"
    _write(destination, folder, files, target=args.runtime_target)
    return 0


if __name__ == "__main__":
    sys.exit(main())
