"""Shared reader for the two `.skills/` lists the shipping gate consults (#371 CR 2).

``doc-check.sh`` resolves ``.skills/doc-sensitive-paths`` (what the gate
watches) and ``.skills/doc-sections`` (what to do about a hit) through ONE
bash function, ``read_list_file``. Both guard modules mirror that function, and
before this module they mirrored it twice — with the copies already drifted
apart in signature. A grammar change upstream had to be found and fixed in two
places, and the drift hid the fact that they were the same mirror.

Importable because ``conftest.py`` in this directory appends it to ``sys.path``,
the same seam ``systemd_units`` uses.
"""

import subprocess
from pathlib import Path

#: Repo root: this file is ``<repo>/scripts/tests/doc_check_lists.py``.
REPO = Path(__file__).resolve().parents[2]

#: The gate both modules pin their premises against. This repo ships the
#: python-fastapi variant of the shipping skill; the other variants carry their
#: own copy of the script with different defaults.
VENDORED = (
    REPO
    / "skills-vendor"
    / "gregoryfoster-skills"
    / "skills"
    / "shipping-work-python-fastapi"
    / "scripts"
    / "doc-check.sh"
)

SENSITIVE_PATHS_FILE = REPO / ".skills" / "doc-sensitive-paths"
DOC_SECTIONS_FILE = REPO / ".skills" / "doc-sections"


def entries(path: Path) -> list[str]:
    """One entry per line, blank lines and `#` comment lines dropped, trimmed.

    Mirrors ``read_list_file`` in the vendored doc-check.sh, including that a
    ``#`` LATER in a line is content rather than a comment — advice cites
    issues — and that a final line with no trailing newline is kept.
    """
    lines = path.read_text().splitlines()
    return [s for line in lines if (s := line.strip()) and not s.startswith("#")]


def tracked() -> list[str]:
    """Every tracked path, as the gate sees them.

    ``core.quotePath=false`` for the reason the gate sets it: git otherwise
    C-quotes any non-ASCII path, and the leading quote defeats anchored
    matching.
    """
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
        ["git", "-c", "core.quotePath=false", "ls-files"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.splitlines()
