# Meta Ads for Flow Steward

Create paused website traffic campaigns, ad sets and single-image Facebook/Instagram feed ads
through typed workflow actions. Extension ID: `flowsteward.meta-ads`, version `1.0.3`.
Runtime: CPython 3.12, public extension SDK 0.4.0+, `extension_host_v2`, Meta API `v27.0`.

## Release status

Version 1.0.3 adds a global **Support** page with links to this repository’s issue
tracker, reporting guidance and diagnostics instructions. It retains the Pillow
13.0.0 JPEG/PNG validation fix from 1.0.2.
Runtime requirements are pinned by version and SHA-256 in
`extension.yaml`; both native Pillow wheels are committed for offline installation.
The manual CI in `.github/workflows/ci.yml` prepares separate AMD64 and ARM64 ZIPs,
checks them with the canonical catalog inspector and tests native offline installation.
Its artifacts are CI candidates; this workflow never creates a tag or GitHub Release.

Account selection uses the native conditional configuration mutation (extension-host
API 1.9.0 or later). It assigns only the selected account and its immutable anchor;
it never writes an OAuth snapshot. Same-account binding can be replayed, while a
different or explicitly null anchor is refused. The host locks and checks the current
configuration atomically; there is no fallback to unconditional upsert on older hosts.

Existing creatives are accepted when all declared enhancement entries are explicitly
`OPT_OUT`; enabled, malformed or other transformation specifications remain unsupported.
The workflow examples select a Facebook-only creative path without an Instagram input,
or an Instagram/both path with an explicit identity. Image input must match both the
requested artifact ID and its `meta_image` binding. POST HTTP 408 remains an unknown
write outcome even if Meta supplies a structured error code.

Live OAuth and actual Meta object acceptance remain unverified. Local checks do not
establish publication readiness or real advertising-account access.

## Support

Open the extension’s **Support** page to report a problem or request a feature.
[Browse existing issues](https://github.com/Flow-Steward/ext-marketing-meta-ads/issues)
before opening a public report. Review any diagnostics before sharing; never include
passwords, API keys, tokens, connection strings or customer data.

## Supported profile

`OUTCOME_TRAFFIC` → website → `LINK_CLICKS`, billed on `IMPRESSIONS`,
`LOWEST_COST_WITHOUT_CAP`. Daily budgets belong to ad sets. Campaign budgets, sharing,
special advertising categories, custom audiences and automatic placements are unsupported.
Choose `facebook`, `instagram` or `both`: Facebook `feed`, Instagram `stream`, and nothing else.

Every campaign/ad set/ad is created `PAUSED`. `set_delivery_status` updates exactly the named
object; it never activates parents or iterates over children. Activating a campaign or ad set
can resume its other eligible advertisements. `ACTIVE` is not proof of delivery or approval.
Use `get_object_status` for configured/effective statuses, parent IDs and available issue codes.
Provider issue text is not echoed because it may contain sensitive material. Missing facts are null.

## Setup

1. The installation operator creates a Meta App, enables Marketing API and configures Facebook
   Login for the intended account access. The operator handles App Review/business verification.
2. In the extension Setup page save App ID/Secret through the native project-scoped OAuth form.
   Register the exact callback URL shown by Flow Steward; do not invent a callback path.
3. Authorize Meta. The post-connect hook reads `/me/permissions` and `/me` and verifies actual
   grants. The OAuth response alone is not treated as proof that all scopes were granted.
4. In **Select an advertising account**, choose the authorized connection. **List available
   advertising accounts** returns IDs, currencies and timezones. Enter a numeric ID without `act_`.
5. Click **Verify and bind account**. This checks access, account state and currency, then saves
   the binding through a native host mutation. Saving settings alone does not verify a binding.
6. Assign this connection to the imported workflow's Meta Ads steps. All writes use the bound
   account; workflow input cannot override it. Create a NEW connection for another account.
7. For Instagram use an explicitly selected professional account linked to the selected Page
   and available to the advertising account. There is no fallback identity.

The extension requests only these scopes: `ads_management` (advertising reads/writes),
`pages_show_list` (Page discovery), `pages_read_engagement` (Page/linked identity metadata),
`pages_manage_ads` (Page-associated advertising), `instagram_basic` (linked professional identity).
It does not request business administration, organic publishing or analytics permissions.
Expired/revoked grants require native reauthorization; this release does not implement a custom
token refresh or long-lived-token exchange outside the host.

## Operations and inputs

Every workflow operation takes a host-resolved `connection_ref`.

| Operation | Main inputs | Main outputs |
| --- | --- | --- |
| list_ad_accounts | optional cursor, limit 1–100 | accounts, nullable next_cursor |
| list_ad_identities | optional cursor, limit 1–100 | Page/Instagram identities, nullable next_cursor |
| create_campaign | name, ad_category=general | campaign_id, ad_account_id, statuses |
| create_ad_set | campaign_id, name, countries, age_min/max, daily_budget, start/end, placements, DSA fields when applicable | ad_set_id, campaign_id, ad_account_id, statuses |
| upload_image | image_artifact | image_hash, ad_account_id |
| create_creative | name, image_hash, page_id, Instagram ID when applicable, placements, primary_text, optional headline, destination_url, cta, optional utm | creative_id, ad_account_id |
| create_ad | name, ad_set_id, creative_id | ad_id, parent IDs, ad_account_id, statuses |
| set_delivery_status | object_type=campaign/ad_set/ad, object_id, status=ACTIVE/PAUSED | object ID/type, statuses, parent IDs, issues |
| get_object_status | object_type=campaign/ad_set/ad, object_id | object ID/type, statuses, parent IDs, issues |

IDs are decimal strings. Names are nonblank and at most 255 characters. Primary text is nonblank,
at most 2000 characters; headline is optional and at most 100. Text is never silently shortened.
Placement-specific rendering may omit fields; equal appearance is not promised.

Countries are unique uppercase ISO alpha-2 codes, 1–50 per group. Ages are explicit integers
18–65 inclusive, minimum ≤ maximum; maximum 65 means 65+. The extension does not infer advertising
category from copy: `ad_category=general` is the user's explicit declaration. Other categories fail.
DSA-regulated countries require BOTH `dsa_beneficiary` and `dsa_payor`; account defaults are not assumed.

`daily_budget` is an exact positive decimal STRING in the account currency, e.g. `"25.00"` EUR.
No floats, currency conversion or rounding. Supported currency precision is explicit in
`meta_ads/validation.py`; unsupported currencies fail. Provider budget minima are enforced by Meta.
Start/end are required ISO 8601 timestamps with offsets; first creation requires a future start
and an end after start. The host replays completed intents before invoking the extension again.

Images must be granted workflow artifacts: JPEG/PNG, one frame, 1080×1080, ≤5 MiB. The extension
streams only native signed artifact grants, decodes/validates with Pillow and uploads unchanged bytes.
It never downloads a user-supplied image URL or resizes/crops the image.

Destination URLs must be absolute HTTPS URLs without credentials. CTA: `LEARN_MORE` or `SHOP_NOW`.
UTM is a closed object containing any of `utm_source`, `utm_medium`, `utm_campaign`, `utm_content`,
`utm_term`, each a nonempty string ≤255 characters. A key already present in the URL is a conflict.
Unrelated URL parameters are preserved. No arbitrary Graph payloads or proxy endpoints exist.

## Existing objects, effects and recovery

Compatible objects may originate in Ads Manager or another integration. They must belong to the
bound account and match the same narrow profile. Unsupported configurations are never rewritten.
Existing campaigns/groups are supplied by ID; a campaign browser/editor is out of scope.

Every Meta write requires a native host `external_effect_id`. Deduplication, concurrent-intent
fencing, durable successful outputs and restart recovery belong to Flow Steward, not a local cache.
The same logical job/step/loop-item intent replays its result; changed input under that intent
conflicts. A new workflow job represents new creation. Separate ACTIVE→PAUSED→ACTIVE steps have
separate intents. Never use a new job to recover an uncertain creation without checking Meta first.

Write timeouts, server failures, malformed creation responses and missing result IDs are
`timeout_unknown`, not safe retries. The extension never searches by name to guess which object
was created. Use the host's existing effect-resolution flow. Successful earlier steps are retained;
there is no automatic rollback/delete. A confirmed write followed by a failed status read keeps
the successful write result and IDs, with unknown status fields.

Test mode suppresses all writes and returns NO fake Meta IDs. It does not simulate a whole
chained campaign. Full-chain tests use an isolated fake Graph transport. Error envelopes expose
stable codes, safe messages and retry facts; raw provider text and access tokens are not returned.
No automatic mutation retries occur inside the subprocess.

## Examples and verification

`examples/new_campaign.yaml` creates paused objects, then activates ad → group → campaign ONLY
when the explicit `activate` input is true. `examples/existing_group.yaml` adds an ad to an existing
group and can activate only that ad. Parent activation in an existing campaign is always explicit.
Templates are disabled by default and require installed-release/project-connection binding.

Run from the matching application checkout using its Docker quality gate:

```sh
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root /absolute/path/to/ext-meta-ads
```

The native test gate validates the bundle, runs smoke and pytest in an offline environment.
Pillow wheels for Linux AMD64 and ARM64 are bundled; validation on one architecture is not an
installation claim for the other. Packaging and dependency locking use the public extension CLI.

No real Meta credentials were supplied for this implementation. Live OAuth, actual scope approval
and paused-object smoke acceptance remain PENDING. Passing mock tests does not prove live operation.
No paid-delivery test, installation or catalog publication is performed automatically.

## References

- https://developers.facebook.com/documentation/ads-commerce/marketing-api
- https://developers.facebook.com/documentation/ads-commerce/marketing-api/get-started/authorization
- https://developers.facebook.com/docs/graph-api/reference/ad-campaign
- https://developers.facebook.com/docs/graph-api/reference/adgroup
- https://pillow.readthedocs.io/en/stable/reference/Image.html

Runtime contracts live in YAML; README explains their use. Follow the target application's
AGENTS.md, standards, extension authoring playbook and publication standard.

## ZIP and CI preparation

The CI repository is public `Flow-Steward/ext-marketing-meta-ads`; the canonical
category is `marketing`. Run **Meta Ads CI (no publication)** manually on the intended
source SHA. Checks use the pinned Core host in fs-audit through `lint.sh`; the tests
with native Core adapters are included. A separate native ARM64 job installs the
same ARM64 archive without network. No Meta credentials are supplied to either job.

Each archive contains only its target's locked Pillow wheel and `.fs-package.yaml`.
The source packager rejects an incorrect wheel hash or a missing compatible wheel.
It omits CI tooling, tests, development wheels and draft catalog data from the runtime
ZIP. Short/detailed/setup descriptions, examples, icon and branding provenance remain.

The validated ZIPs and source-bound `release-plan.json` are retained as GitHub Actions
artifacts for seven days. Download them before expiration. Publication is an explicit
later operation; passing this CI does not certify live OAuth or Meta ad delivery.

Public release publication uses the separate manual `publish.yml` workflow.
It consumes only a successful source-bound `release-prep` run of the native CI above.
The release owner records the exact approved extension SHA and published Core
v0.9.18 SHA before supplying a new immutable tag. All ZIP builds and release
asset uploads occur in CI; the shared publisher verifies the tag/source,
requires the repository to remain public and verifies downloaded asset bytes.
Catalog registration is coordinated separately by the catalog owner.

Version 1.0.1 introduces an original violet campaign/megaphone icon. It is
independent Flow Steward artwork, with provenance and SHA-256 in `BRANDING.md`.
Runtime, permissions, OAuth configuration and dependencies are unchanged from
1.0.0. The release-prep guard compares all runtime inputs to that accepted
release, allowing only the version and public-reference listing metadata edits;
packaging tests and both native archive checks run fresh.
