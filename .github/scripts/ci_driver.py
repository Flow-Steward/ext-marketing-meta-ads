"""GitHub-only container orchestration for Meta Ads checks and ZIP preparation."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Any

CORE = "2260894d4e13ada0452a681e0be616eb4463e7ab"
CATALOG = "4ad7b1131c72c381d6bcbb5613f209b077a01ce0"
REPOSITORY = "Flow-Steward/ext-marketing-meta-ads"


def run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[Any]:
    safe = ["<resource label>" if "resource-token=" in arg else arg for arg in args]
    print("[ci] " + " ".join(safe), flush=True)
    return subprocess.run(args, check=True, **kwargs)


def output(args: list[str]) -> str:
    return subprocess.check_output(args, text=True).strip()


def main() -> None:
    if (
        os.environ.get("GITHUB_ACTIONS") != "true"
        or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted"
        or os.environ.get("GITHUB_REPOSITORY") != REPOSITORY
        or "FS_TASK_RESOURCE_OWNER" not in os.environ
        or sys.argv[1:] not in (["quality-package"], ["verify-arm64"])
    ):
        raise SystemExit("This driver requires the managed, identified GitHub job")
    mode = sys.argv[1]
    core = Path.cwd()
    source = core / "candidate"
    catalog = core / "catalog-tooling"
    if output(["git", "rev-parse", "HEAD"]) != CORE:
        raise SystemExit("Core checkout differs from the pinned host")
    if output(["git", "-C", str(catalog), "rev-parse", "HEAD"]) != CATALOG:
        raise SystemExit("Catalog tooling differs from the immutable inspector")
    sha = output(["git", "-C", str(source), "rev-parse", "HEAD"])
    if sha != os.environ["GITHUB_SHA"]:
        raise SystemExit("Extension checkout differs from the dispatched SHA")
    run(["git", "-C", str(source), "diff", "--quiet", "HEAD", "--"])
    registry = Path(os.environ["RUNNER_TEMP"]) / "meta-ads-ownership"
    owner = os.environ["FS_TASK_RESOURCE_OWNER"]
    scratch = Path(os.environ["FS_TASK_SCRATCH"])
    artifact = source / ".ci-artifacts"
    artifact.mkdir(exist_ok=True)
    # The Docker context contains only immutable Core files, not this private candidate.
    export = scratch / "core-context"
    export.mkdir()
    archive = scratch / "core.tar"
    with archive.open("wb") as stream:
        run(["git", "archive", CORE], stdout=stream)
    with tarfile.open(archive) as bundle:
        bundle.extractall(export, filter="data")
    digest = output(["bash", "core/entrypoints/cli/scripts/lint.sh", "--audit-toolchain-digest"])
    tag = os.environ["FS_TASK_IMAGE_PREFIX"] + ":audit"
    labels = {
        "org.flowsteward.task": owner,
        "org.flowsteward.resource-token": os.environ["FS_TASK_RESOURCE_TOKEN"],
        "org.flowsteward.disposable": "true",
    }
    label_args = [item for key, value in labels.items() for item in ("--label", key + "=" + value)]
    run(
        [
            "docker",
            "buildx",
            "build",
            "--load",
            "--target",
            "audit",
            "--tag",
            tag,
            "--build-arg",
            "AUDIT_TOOLCHAIN_DIGEST=" + digest,
            "--build-arg",
            "BUILD_SHA=" + CORE,
            *label_args,
            "--file",
            str(export / "infra/docker/Dockerfile"),
            str(export),
        ]
    )
    image = output(["docker", "image", "inspect", tag, "--format", "{{.Id}}"])
    actual_digest = output(
        [
            "docker",
            "image",
            "inspect",
            image,
            "--format",
            '{{ index .Config.Labels "org.flowsteward.audit-toolchain" }}',
        ]
    )
    if actual_digest != digest:
        raise SystemExit("Built audit image has the wrong toolchain identity")

    def checks(suffix: str, targets: str, static: bool = False) -> None:
        identity = owner + "-" + suffix
        base = [
            sys.executable,
            "-m",
            "core.entrypoints.cli.agent_resources",
            "--registry",
            str(registry),
            "--owner",
            identity,
        ]
        env = dict(os.environ, FS_BORROWED_AUDIT_IMAGE=image, FS_STACK_ENV_FILE="/dev/null")
        try:
            run(
                [
                    *base,
                    "run",
                    "--",
                    "bash",
                    "core/entrypoints/cli/scripts/lint.sh",
                    "--checks",
                    "extension-static,extension-tests" if static else "extension-tests",
                    "--extension-root",
                    str(source),
                    "--test-paths",
                    targets,
                ],
                env=env,
            )
        finally:
            run([*base, "cleanup", "--apply"], env=env)

    if mode == "quality-package":
        checks(
            "source",
            " ".join(
                [
                    "tests/test_validation.py",
                    "tests/test_operations.py",
                    "tests/test_transport.py",
                    "tests/test_setup_and_boundaries.py",
                    "tests/test_review_regressions.py",
                    "tests/test_packaging.py",
                ]
            ),
            static=True,
        )
        command = [
            "docker",
            "run",
            "--rm",
            "--pull",
            "never",
            "--network",
            "none",
            "--read-only",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--tmpfs",
            "/tmp:rw,exec",
            "--env",
            "HOME=/tmp",
            "--env",
            "PYTHONDONTWRITEBYTECODE=1",
            "--env",
            "GIT_CONFIG_COUNT=1",
            "--env",
            "GIT_CONFIG_KEY_0=safe.directory",
            "--env",
            "GIT_CONFIG_VALUE_0=/source",
            "--env",
            "GIT_OPTIONAL_LOCKS=0",
            *label_args,
            "--mount",
            f"type=bind,src={source},dst=/source,readonly",
            "--mount",
            f"type=bind,src={catalog},dst=/tooling,readonly",
            "--mount",
            f"type=bind,src={artifact},dst=/out",
            image,
            "python",
            "/tooling/scripts/release_extension.py",
        ]
        # The caller has no publication path: these are local build/inspection verbs only.
        run(
            [
                *command,
                "build",
                "--source-root",
                "/source",
                "--repository",
                REPOSITORY,
                "--tag",
                "v1.0.0",
                "--config",
                ".github/release-config.json",
                "--out-dir",
                "/out",
                "--catalog-root",
                "/tooling",
            ]
        )
        run(
            [
                *command,
                "verify-plan",
                "--artifact-dir",
                "/out",
                "--repository",
                REPOSITORY,
                "--tag",
                "v1.0.0",
                "--source-sha",
                sha,
                "--catalog-root",
                "/tooling",
            ]
        )
    checks("archive", "tests/test_package_install.py")
    result = {
        "source_sha": sha,
        "core_sha": CORE,
        "catalog_sha": CATALOG,
        "mode": mode,
        "audit_toolchain": digest,
        "audit_image": image,
        "architecture": output(["docker", "info", "--format", "{{.Architecture}}"]),
        "publication": "not started",
        "live_meta": "not attempted",
    }
    (artifact / (mode + "-evidence.json")).write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
