"""The SocratiCode pin is one version, spelled in four tracked places (#415).

``.claude/settings.json`` declares ``SOCRATICODE_SPEC`` — the session server's
launch spec, the value ``preflight.sh`` compares against — and three docs state
the version the host is pinned to. A re-pin changes them together
(docs/SOCRATICODE.md § The server is pinned); one that misses a doc leaves it
naming a version the host no longer runs, and nothing else would notice. The
host-side halves (the pre-install, the VS Code machine setting) are not in the
repo, so ``preflight.sh`` — not this test — is what checks those.
"""

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SETTINGS = REPO / ".claude" / "settings.json"
DOCS = REPO / "docs"


def _pinned_version() -> str:
    """The exact version ``SOCRATICODE_SPEC`` declares — never a range or a tag."""
    spec = json.loads(SETTINGS.read_text())["env"]["SOCRATICODE_SPEC"]
    match = re.fullmatch(r"socraticode@(\d+\.\d+\.\d+)", spec)
    assert match, f"SOCRATICODE_SPEC={spec!r} is not an exact version; a floating spec pins nothing"
    return match.group(1)


def test_the_session_spec_is_an_exact_version() -> None:
    """``@latest`` or a range would re-open the install at every session start."""
    _pinned_version()


@pytest.mark.parametrize(
    ("doc", "phrase"),
    [
        ("SOCRATICODE.md", "Pinned here at **{v}**"),
        ("SOCRATICODE.md", "pinned install v{v} "),
        ("SOCRATICODE.md", "`SOCRATICODE_SPEC=socraticode@{v}`"),
        ("DEPLOYMENT-HOST.md", "SocratiCode pinned to {v} "),
        ("ENVIRONMENT.md", "`socraticode@{v}`"),
    ],
)
def test_every_doc_names_the_declared_pin(doc: str, phrase: str) -> None:
    """Each doc's statement of the pin names the version settings.json declares."""
    expected = phrase.format(v=_pinned_version())
    assert expected in (DOCS / doc).read_text(), f"docs/{doc} does not say {expected!r}"
