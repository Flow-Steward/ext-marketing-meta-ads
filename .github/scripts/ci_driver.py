"""GitHub-only container orchestration for Meta Ads checks and ZIP preparation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
from typing import Any

CORE = "2260894d4e13ada0452a681e0be616eb4463e7ab"
CATALOG = "02c0f6f19b665c46300d42e790ef74375f3d0e67"
REPOSITORY = "Flow-Steward/ext-marketing-meta-ads"
RELEASE_BASE = "0e03a61d954e4c25bb43222f56afd1133749c031"


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
    scope = os.environ.get("META_ADS_CHECK_SCOPE", "source")
    if scope not in {"source", "packaging-recovery", "artifact-recovery", "release-prep"}:
        raise SystemExit("Unknown check scope")
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
    if scope == "release-prep":
        # Only release metadata/artwork and freshly tested packaging inputs may differ.
        run(
            [
                "git",
                "-C",
                str(source),
                "diff",
                "--quiet",
                RELEASE_BASE,
                sha,
                "--",
                ".",
                ":(exclude).github/**",
                ":(exclude)docs/**",
                ":(exclude)README.md",
                ":(exclude)BRANDING.md",
                ":(exclude)icon.png",
                ":(exclude)extension.yaml",
                ":(exclude)extension-descriptor.yaml",
                ":(exclude)tests/test_packaging.py",
                ":(exclude)tests/test_package_install.py",
            ]
        )
        # Compare full bytes after the three exact permitted release metadata edits.
        # Permissions, grants, runtime and dependencies cannot be normalized away.
        for name, edits in {
            "extension.yaml": [('version: "1.0.0"\n', 'version: "1.0.1"\n')],
            "extension-descriptor.yaml": [
                ('release_version: "1.0.0"\n', 'release_version: "1.0.1"\n'),
                ('  visibility: "private"\n', '  visibility: "public_reference"\n'),
            ],
        }.items():
            expected = subprocess.check_output(
                ["git", "-C", str(source), "show", f"{RELEASE_BASE}:{name}"]
            )
            for before, after in edits:
                if expected.count(before.encode()) != 1:
                    raise SystemExit("Release metadata baseline is ambiguous")
                expected = expected.replace(before.encode(), after.encode(), 1)
            if (source / name).read_bytes() != expected:
                raise SystemExit(f"Unexpected runtime/permission or metadata change: {name}")
    registry = Path(os.environ["RUNNER_TEMP"]) / "meta-ads-ownership"
    owner = os.environ["FS_TASK_RESOURCE_OWNER"]
    scratch = Path(os.environ["FS_TASK_SCRATCH"])
    artifact = source / ".ci-artifacts"
    artifact.mkdir(exist_ok=True)
    # The Docker context contains only immutable Core files, not the extension candidate.
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

    # Align hosted writer IDs: nested caches must remain removable by the runner.
    uid_override = scratch / "audit-uid.yaml"
    uid_override.write_text(
        f'services:\n  fs-audit:\n    user: "{os.getuid()}:{os.getgid()}"\n'
        "    environment:\n      HOME: /tmp\n"
    )

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
        env = dict(
            os.environ,
            FS_BORROWED_AUDIT_IMAGE=image,
            FS_STACK_ENV_FILE="/dev/null",
            FS_META_ADS_UID_OVERRIDE=str(uid_override),
        )
        try:
            run(
                [
                    *base,
                    "run",
                    "--",
                    "bash",
                    "-c",
                    'export FS_COMPOSE_FILES="${FS_COMPOSE_FILES:?} ${FS_META_ADS_UID_OVERRIDE:?}"; '
                    'exec "$@"',
                    "meta-ads-managed-gate",
                    "bash",
                    "core/entrypoints/cli/scripts/lint.sh",
                    "--checks",
                    (
                        "extension-static"
                        if static and not targets
                        else "extension-static,extension-tests"
                        if static
                        else "extension-tests"
                    ),
                    "--extension-root",
                    str(source),
                    *(["--test-paths", targets] if targets else []),
                ],
                env=env,
            )
        finally:
            run([*base, "cleanup", "--apply"], env=env)

    if mode == "quality-package":
        if scope == "artifact-recovery":
            checks("ci-static", "", static=True)
        else:
            checks(
                "source",
                (
                    "tests/test_packaging.py::"
                    "test_explicit_target_and_output_produce_only_one_compatible_archive"
                    if scope == "packaging-recovery"
                    else "tests/test_packaging.py"
                    if scope == "release-prep"
                    else " ".join(
                        [
                            "tests/test_validation.py",
                            "tests/test_image_security.py",
                            "tests/test_operations.py",
                            "tests/test_transport.py",
                            "tests/test_setup_and_boundaries.py",
                            "tests/test_review_regressions.py",
                            "tests/test_packaging.py",
                        ]
                    )
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
                "v1.0.1",
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
                "v1.0.1",
                "--source-sha",
                sha,
                "--catalog-root",
                "/tooling",
            ]
        )
    if mode == "quality-package" and scope == "artifact-recovery":
        # Reuse only the exact native AMD64 bytes accepted by run43762111336.
        expected = {
            "flowsteward.meta-ads-1.0.0-linux-amd64.zip": "608281c1f9f891b436deb67412b0430fa481ba69219edff24aa98374f9360d90",
            "flowsteward.meta-ads-1.0.0-linux-arm64.zip": "0c192405fa661355786cf59fd274e1f3682dec654f2ae5565a21659ec5c90788",
        }
        for name, archive_digest in expected.items():
            if hashlib.sha256((artifact / name).read_bytes()).hexdigest() != archive_digest:
                raise SystemExit("Archive differs from the previously accepted recovery bytes")
    else:
        checks("archive", "tests/test_package_install.py")
    result = {
        "source_sha": sha,
        "core_sha": CORE,
        "catalog_sha": CATALOG,
        "mode": mode,
        "check_scope": scope,
        "prior_source_pass_sha": (
            "b5edea676a18bc208ca6a530e63e5261d6775ee9" if scope != "source" else None
        ),
        "runtime_implementation_equal": RELEASE_BASE if scope == "release-prep" else None,
        "permitted_release_metadata": (
            ["version:1.0.0->1.0.1", "listing.visibility:private->public_reference"]
            if scope == "release-prep"
            else None
        ),
        "prior_amd64_archive_pass": (
            {"source_sha": "76130f1cb4b64a21cdd23492f43510baf60aaff7", "run_id": 43762111336}
            if mode == "quality-package" and scope == "artifact-recovery"
            else None
        ),
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
