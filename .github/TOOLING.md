# CI-only packaging provenance

The package CLI is adapted from the maintained source template in
Flow-Steward/extensions at 4ad7b1131c72c381d6bcbb5613f209b077a01ce0,
`templates/single-extension/.github/scripts/package_extension.py`.
Target/output flags, native lock verification, deterministic metadata and
runtime-only source selection are explicit Meta Ads adaptations.

The mandatory recipe builder and catalog inspector are checked out at the same
immutable commit. The workflow has only workflow_dispatch, contents:read and
artifact upload; it has no publisher or tag-trigger path. The version string
v1.0.0 passed to the recipe identifies candidate contents; no Git tag is created.

Core2260894d4e13ada0452a681e0be616eb4463e7ab supplies the canonical fs-audit toolchain,
lint registry and host validator. Its hosted-quality.sh is intentionally not reused:
that adapter identifies the Core repository, whereas this CI identifies its own
private extension repository. Managed generic resource ownership is used instead.
