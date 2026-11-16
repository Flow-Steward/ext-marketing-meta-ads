"""Required CI-only native archive acceptance; absent artifacts fail, never skip."""

import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import venv
import zipfile
import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_archive_installs_offline_on_the_native_runner(tmp_path):
    target = {"x86_64": "linux-amd64", "aarch64": "linux-arm64"}[platform.machine()]
    artifacts = ROOT / ".ci-artifacts"
    path = artifacts / f"flowsteward.meta-ads-1.0.1-{target}.zip"
    assert path.is_file(), "Required CI archive is absent"
    plan = json.loads((artifacts / "release-plan.json").read_text())
    asset = next(row for row in plan["assets"] if row["filename"] == path.name)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == asset["sha256"]
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        for name in archive.namelist():
            rel = Path(name)
            assert not rel.is_absolute() and ".." not in rel.parts
        archive.extractall(tmp_path / "unpacked")
    extension = tmp_path / "unpacked/flowsteward_meta_ads"
    manifest = yaml.safe_load((extension / "extension.yaml").read_text())
    metadata = yaml.safe_load((extension / ".fs-package.yaml").read_text())
    assert metadata["runtime_target"] == target
    assert manifest["version"] == "1.0.1"
    assert hashlib.sha256((extension / "icon.png").read_bytes()).hexdigest() == (
        "a3e8a46ec3f24ceee55174b7be045ae8d1b57661fc6e707e20fa12562d750657"
    )
    assert manifest["listing"]["category"] == "marketing"
    descriptor = yaml.safe_load((extension / "extension-descriptor.yaml").read_text())
    assert descriptor["release_version"] == manifest["version"]
    assert descriptor["listing"]["visibility"] == "public_reference"
    assert descriptor["runtime"]["compatibility"]["platform_min"] == "1.9.0"
    assert descriptor["identity"]["description"] == manifest["description"]
    assert descriptor["listing"]["description"] == manifest["listing"]["description"]
    assert descriptor["listing"]["setup_summary"] == manifest["listing"]["setup_summary"]
    assert (extension / "icon.png").read_bytes() == (ROOT / "icon.png").read_bytes()
    assert (extension / "BRANDING.md").read_bytes() == (ROOT / "BRANDING.md").read_bytes()
    requirements = tmp_path / "locked.txt"
    requirements.write_text(
        "\n".join(
            row["name"]
            + "=="
            + row["version"]
            + " "
            + " ".join("--hash=" + h for h in row["hashes"])
            for row in manifest["python_requirements"]
        )
        + "\n"
    )
    runtime = tmp_path / "runtime"
    venv.EnvBuilder(with_pip=True).create(runtime)
    python = runtime / "bin/python"
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--no-index",
            "--no-deps",
            "--require-hashes",
            "--find-links",
            str(extension / "wheels"),
            "-r",
            str(requirements),
        ],
        check=True,
    )
    sdk = next((ROOT / "dev-wheels").glob("flowsteward_extension_sdk-*.whl"))
    assert (
        hashlib.sha256(sdk.read_bytes()).hexdigest()
        == "ffb5f3764d5a384b4441f6e3c0c4e66f5887d57c156fdc93abf70b5d0eeb22af"
    )
    subprocess.run(
        [str(python), "-m", "pip", "install", "--no-index", "--no-deps", str(sdk)], check=True
    )
    subprocess.run(
        [
            str(python),
            "-c",
            "from PIL import Image; import PIL; assert PIL.__version__ == '12.3.0'; Image.new('RGB',(1,1)).load()",
        ],
        check=True,
    )
    runtime_env = {
        key: value for key, value in os.environ.items() if key not in {"PYTHONPATH", "PYTHONHOME"}
    }
    runtime_env["PYTHONNOUSERSITE"] = "1"
    imported = subprocess.run(
        [
            str(python),
            "-c",
            "from pathlib import Path; import meta_ads.runtime; "
            "assert Path(meta_ads.runtime.__file__).resolve().is_relative_to(Path.cwd().resolve())",
        ],
        cwd=extension,
        env=runtime_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert imported.returncode == 0, imported.stderr
    request = {
        "contract_version": "extension_host_v2",
        "mode": "handshake",
        "runtime_context": {
            "group_id": None,
            "for_each_id": None,
            "step_id": None,
            "step_execution_id": None,
        },
    }
    handshake = subprocess.run(
        [str(python), "main.py"],
        input=json.dumps(request),
        cwd=extension,
        env=runtime_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert handshake.returncode == 0 and not handshake.stderr, handshake.stderr
    assert json.loads(handshake.stdout)["result"]["contract_version"] == "extension_host_v2"
    health = subprocess.run(
        [str(python), "health.py"],
        cwd=extension,
        env=runtime_env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert health.returncode == 0 and not health.stderr, health.stderr
    assert json.loads(health.stdout)["status"] == "healthy"
    env = dict(os.environ, FS_STACK_ENVIRONMENT="development")
    validated = subprocess.run(
        [
            sys.executable,
            "-m",
            "core.entrypoints.cli",
            "extensions",
            "validate",
            "flowsteward.meta-ads",
            "--root",
            str(extension.parent),
            "--marketplace",
            "--no-health",
        ],
        cwd="/app",
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    assert validated.returncode == 0, validated.stdout + validated.stderr
