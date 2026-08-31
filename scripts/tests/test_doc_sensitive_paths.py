"""The shipping gate's sensitive-path list, tailored to this repo (#297).

``doc-check.sh`` (Step 1.5 of the shipping skill) flags changed files whose
names or structure the docs enumerate, so the matching doc section gets a look
before the branch ships. Upstream gregoryfoster/skills#252 fixed two defects in
it: entries were anchored at the START of the path, so ``src/`` matched
``src/foo.py`` but never ``packages/<pkg>/src/foo.py``, and a list where nothing
could match printed the same clean green as a genuinely doc-neutral branch.

On this workspace — 11 packages, every source file under ``packages/*/src/`` —
the root-anchored matcher never saw a single one of them, and 6 of the 12
built-in entries (``CHANGELOG.md``, ``schema.sql``, ``src/api/``,
``src/models/``, ``src/core/``, ``.env.example``) name nothing that exists here.
The gate reported "No sensitive paths changed" and exited 0 on branches that
renamed a workspace member.

``.skills/doc-sensitive-paths`` replaces the defaults wholesale. These tests
pin the two properties that make it worth having: it is tailored (none of the
six dead defaults survive) and it is LIVE — every entry matches at least one
tracked file, so the gate's green means the list looked and found nothing
rather than that it could not have found anything.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
LIST_FILE = REPO / ".skills" / "doc-sensitive-paths"
VENDORED = (
    REPO
    / "skills-vendor"
    / "gregoryfoster-skills"
    / "skills"
    / "shipping-work-python-fastapi"
    / "scripts"
    / "doc-check.sh"
)

#: Built-in entries that name nothing in this tree. Committing the defaults
#: unchanged would make Step 1.5 print a note about each on every clean run.
DEAD_DEFAULTS = frozenset(
    {"CHANGELOG.md", "schema.sql", "src/api/", "src/models/", "src/core/", ".env.example"}
)


def _entries() -> list[str]:
    """The file's grammar: one path per line, blank lines and `#` comments out."""
    lines = LIST_FILE.read_text().splitlines()
    return [s for line in lines if (s := line.strip()) and not s.startswith("#")]


def _tracked() -> list[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
        ["git", "-c", "core.quotePath=false", "ls-files"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.splitlines()


def _matches(file: str, entry: str) -> bool:
    """Mirror of ``path_matches`` in the vendored doc-check.sh.

    Entries match whole path SEGMENTS at any depth. A trailing slash is the
    convention for a directory; a slash-less entry names a file or a directory,
    and every continuation requires a literal ``/`` — which is what keeps
    ``pyproject.toml`` from also claiming ``pyproject.toml.bak``.
    """
    if entry.endswith("/"):
        return file.startswith(entry) or f"/{entry}" in file
    return (
        file == entry
        or file.endswith(f"/{entry}")
        or file.startswith(f"{entry}/")
        or f"/{entry}/" in file
    )


def test_the_list_exists_and_parses() -> None:
    assert LIST_FILE.is_file(), (
        "no .skills/doc-sensitive-paths; the built-in defaults miss every file "
        "under packages/*/src/ in this workspace (#297)"
    )
    assert _entries(), "an empty list is exit 2 upstream, not a pass — remove the file instead"


def test_no_dead_default_survived_the_tailoring() -> None:
    """Copying the defaults would make every clean run print a note about these."""
    kept = DEAD_DEFAULTS & set(_entries())
    assert not kept, f"entries that name nothing in this tree: {sorted(kept)}"


def test_every_entry_matches_a_tracked_file() -> None:
    """A dead entry cannot contribute to a verdict, and reads as if it had.

    This is the whole of #252's second defect, scoped to our own list: an entry
    that matches nothing is indistinguishable from an entry that matched and
    found no change.
    """
    tracked = _tracked()
    dead = [entry for entry in _entries() if not any(_matches(f, entry) for f in tracked)]
    assert not dead, f"entries matching no tracked file: {dead}"


def test_entries_are_unique() -> None:
    entries = _entries()
    assert len(entries) == len(set(entries)), "duplicate entries"


def test_the_vendored_matcher_is_still_segment_based() -> None:
    """`_matches` above mirrors the vendor; pin the premise it mirrors.

    A vendored script that went back to root-anchored matching would leave this
    file asserting a liveness the gate no longer has — and the failure mode is
    the silent one #252 was filed for.
    """
    assert VENDORED.is_file(), f"vendored gate missing at {VENDORED}"
    source = VENDORED.read_text()
    assert "path_matches()" in source, (
        "the vendored doc-check.sh has no segment matcher; refresh "
        "skills-vendor/gregoryfoster-skills (gregoryfoster/skills#252)"
    )
    assert ".skills/doc-sensitive-paths" in source, (
        "the vendored doc-check.sh does not read the project's path list"
    )
