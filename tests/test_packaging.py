"""Real package CLI regressions; fixtures contain the committed runtime wheels."""

from pathlib import Path
import shutil
import os
import subprocess
import sys
import zipfile
import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]


def git(root, *args):
    return subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            *args,
        ],
        capture_output=True,
        text=True,
        check=True,
    )


@pytest.fixture
def package_source(tmp_path):
    source = tmp_path / "source"
    shutil.copytree(
        ROOT,
        source,
        ignore=shutil.ignore_patterns(
            ".git",
            "dist",
            "logs",
            ".ci-artifacts",
            "__pycache__",
            ".pytest_cache",
            ".fs-python",
            ".venv",
            ".ruff_cache",
            ".mypy_cache",
        ),
    )
    git(source, "init", "-q")
    git(source, "add", ".")
    git(source, "commit", "-qm", "Fixture source")
    return source


def package(source, out, target):
    return subprocess.run(
        [
            sys.executable,
            str(source / ".github/scripts/package_extension.py"),
            "--runtime-target",
            target,
            "--out-dir",
            str(out),
        ],
        cwd=source,
        capture_output=True,
        text=True,
        check=False,
    )


def test_explicit_target_and_output_produce_only_one_compatible_archive(package_source, tmp_path):
    out = tmp_path / "output"
    result = package(package_source, out, "linux-amd64")
    assert result.returncode == 0, result.stderr
    expected = out / "flowsteward.meta-ads-1.0.1-linux-amd64.zip"
    assert expected.is_file(), result.stdout
    assert list(out.iterdir()) == [expected]
    with zipfile.ZipFile(expected) as archive:
        assert archive.testzip() is None
        names = archive.namelist()
        metadata = yaml.safe_load(archive.read("flowsteward_meta_ads/.fs-package.yaml"))
        assert metadata["runtime_target"] == "linux-amd64"
        wheels = [name for name in names if name.endswith(".whl")]
        assert len(wheels) == 1 and "x86_64" in wheels[0]
        assert "flowsteward_meta_ads/icon.png" in names
        assert "flowsteward_meta_ads/BRANDING.md" in names
        assert not any(
            "/.github/" in name or "/dev-wheels/" in name or "/.git/" in name for name in names
        )


def test_corrupted_locked_wheel_is_refused(package_source, tmp_path):
    wheel = next((package_source / "wheels").glob("*x86_64.whl"))
    wheel.write_bytes(wheel.read_bytes() + b"corrupt")
    git(package_source, "add", "wheels")
    git(package_source, "commit", "-qm", "Corrupt fixture wheel")
    result = package(package_source, tmp_path / "output", "linux-amd64")
    assert result.returncode != 0, "corrupt locked wheel was packaged"
    assert "sha256" in (result.stdout + result.stderr).lower()


def test_missing_target_wheel_is_refused(package_source, tmp_path):
    wheel = next((package_source / "wheels").glob("*aarch64.whl"))
    wheel.unlink()
    git(package_source, "add", "wheels")
    git(package_source, "commit", "-qm", "Remove fixture target")
    result = package(package_source, tmp_path / "output", "linux-arm64")
    assert result.returncode != 0, "archive without its required native wheel was packaged"


def test_package_bytes_are_reproducible(package_source, tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    for index, out in enumerate((first, second)):
        if index:
            for path in package_source.rglob("*"):
                if path.is_file() and ".git" not in path.parts:
                    os.utime(path, (1577836800, 1577836800))
        result = package(package_source, out, "linux-amd64")
        assert result.returncode == 0, result.stderr
    assert next(first.glob("*.zip")).read_bytes() == next(second.glob("*.zip")).read_bytes()
