# CI-only packaging provenance

The package CLI is adapted from the maintained source template in
Flow-Steward/extensions at 4ad7b1131c72c381d6bcbb5613f209b077a01ce0,
`templates/single-extension/.github/scripts/package_extension.py`.
Target/output flags, native lock verification, deterministic metadata and
runtime-only source selection are explicit Meta Ads adaptations.

The mandatory recipe builder/catalog inspector and shared private publisher are
pinned at 02c0f6f19b665c46300d42e790ef74375f3d0e67. The archive/recipe inspector
code is unchanged from 4ad7b113; the shared publisher adds explicit visibility
validation. No local publisher or visibility policy fork is maintained here.

ci.yml stays workflow_dispatch/contents:read and never creates a Git tag or release.
Its release-prep scope runs only changed static/packager tests and native archive
smoke on both targets. Runtime source inputs must match the recorded b5edea6 pass
outside the exact declared CI/docs/artwork exclusions before reuse is recorded.
The source-bound plan includes the reviewed versioned notes as an additional asset.

publish-private.yml is a SEPARATE manual workflow. It selects an exact successful
same-source CI run, checks its two native jobs/evidence, stages only listed plan
payload, and reuses the shared verify-plan and publisher with
--expected-visibility private. The read-only preflight must pass before the sole
contents:write job; the shared private guard is reused before publication and
before/after release asset readback. No repository/package visibility or catalog
API is called. No automatic push/tag/release trigger and no PAT is used.

ROOT GO must be recorded as repository variables META_ADS_RELEASE_APPROVED_SHA
and META_ADS_RELEASE_APPROVED_CORE_SHA, matching the exact main/dispatch source and
stable published Core v0.9.18 tag commit. They are intentionally NOT set during
preparation. The release owner accepts host compatibility before recording that
Core SHA; candidate CI remains pinned to the documented tested host. The release
owner supplies the immutable v1.0.0 tag at the approved source only after that GO,
as required by the shared publisher. CI verifies it before uploading any ZIP;
there is no local tag creation or repair path. Existing tags/assets are verified;
conflicting or partial outcomes are not repaired or retried.

Core2260894d4e13ada0452a681e0be616eb4463e7ab supplies the canonical fs-audit toolchain,
lint registry and host validator. Its hosted-quality.sh is intentionally not reused:
that adapter identifies the Core repository, whereas this CI identifies its own
private extension repository. Managed generic resource ownership is used instead.
