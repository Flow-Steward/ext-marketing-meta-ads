"""The public Support route is available without a project connection."""

from pathlib import Path
import re

import yaml

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/Flow-Steward/ext-marketing-meta-ads"


def assert_support_page(root):
    manifest = yaml.safe_load((root / "extension.yaml").read_text())
    descriptor = yaml.safe_load((root / "extension-descriptor.yaml").read_text())
    ui = yaml.safe_load((root / manifest["ui_manifest"]).read_text())
    support = [page for page in ui["pages"] if page["page_id"] == "support"]
    assert len(support) == 1, "Register exactly one global Support page"
    assert support[0]["title"] == "Support"
    assert support[0]["ref"] == "pages/support.yaml"
    page = yaml.safe_load((root / "ui" / support[0]["ref"]).read_text())
    assert page["page_id"] == "support"
    assert page["title"] == "Support"
    assert page["project_scoped"] is False
    assert "connection" not in page
    assert all(component["type"] == "markdown" for component in page["components"])
    body = "\n".join(component["body"] for component in page["components"])
    assert set(re.findall(r"\]\((https://[^)]+)\)", body)) == {
        REPOSITORY + "/issues/new/choose",
        REPOSITORY + "/issues",
        REPOSITORY,
    }
    for guidance in (
        "GitHub account",
        "extension version",
        "Flow Steward version",
        "expected",
        "happened instead",
        "exact error message",
        "workflow, step or action",
        "when it happened",
        "Monitoring → Download diagnostics",
        "review before sharing",
        "Issues are public",
        "Never include passwords, API keys, tokens, connection strings or customer data",
    ):
        assert guidance in body
    assert manifest["vendor"]["support_url"] == REPOSITORY + "/issues"
    assert descriptor["vendor"]["support_url"] == manifest["vendor"]["support_url"]


def test_support_is_registered_with_own_public_help_and_safe_reporting():
    assert_support_page(ROOT)
