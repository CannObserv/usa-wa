"""Tests for the vendored-skill inventory: skills/, .claude/skills/, docs/SKILLS.md (#422).

Vendoring a skill is a manual, three-part step: the daily auto-refresh hook
bumps the submodule *pointer* but never creates a per-skill symlink, so each
new skill needs a symlink in both discovery directories and a row in the doc.
Nothing else notices when one of the three is missed, so each is asserted here:

  * every ``skills/`` entry has its ``.claude/skills/`` mirror, and vice versa;
  * every entry resolves to a directory carrying a ``SKILL.md`` (skipped when
    the submodules are unpopulated — a fresh worktree, #296's sibling);
  * every skill linked from ``gregoryfoster-skills`` is listed in the doc's
    vendor table, by its backticked name.
"""

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]  # scripts/tests/ → repo root
SKILLS = REPO / "skills"
CLAUDE_SKILLS = REPO / ".claude" / "skills"
SKILLS_DOC = REPO / "docs" / "SKILLS.md"
GF_VENDOR = "skills-vendor/gregoryfoster-skills/skills/"
GF_TABLE_HEADING = "## Vendor skills (from gregoryfoster-skills)"


def _names(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir()}


def _gf_table_names() -> set[str]:
    """Backticked skill names in the first column of the gregoryfoster vendor table."""
    section = SKILLS_DOC.read_text().split(GF_TABLE_HEADING, 1)[1].split("\n## ", 1)[0]
    return set(re.findall(r"^\| `([a-z0-9-]+)` \|", section, flags=re.MULTILINE))


def test_claude_skills_mirrors_skills():
    """``.claude/skills/`` carries exactly the entries in ``skills/``."""
    assert _names(CLAUDE_SKILLS) == _names(SKILLS)


def test_every_mirror_points_into_skills():
    """Each ``.claude/skills/<name>`` is the relative link ``../../skills/<name>``."""
    for entry in CLAUDE_SKILLS.iterdir():
        assert entry.is_symlink(), entry
        assert str(entry.readlink()) == f"../../skills/{entry.name}", entry


def test_every_skill_resolves_to_a_skill_md():
    """Each ``skills/<name>`` reaches a ``SKILL.md`` — no dangling vendor link."""
    if not any((REPO / "skills-vendor").glob("*/skills")):
        pytest.skip("skills-vendor/ unpopulated: git submodule update --init --recursive")
    for entry in SKILLS.iterdir():
        assert (entry / "SKILL.md").is_file(), entry


def test_every_gregoryfoster_skill_is_listed_in_the_doc():
    """A skill linked from gregoryfoster-skills has a row in the doc's vendor table."""
    linked = {e.name for e in SKILLS.iterdir() if e.is_symlink() and GF_VENDOR in str(e.readlink())}
    assert linked - _gf_table_names() == set()
