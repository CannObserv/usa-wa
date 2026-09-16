"""Pin MODULES-DEPLOYMENT.md's Layer-4 tree against the tree it describes (#373).

The ratchet #167 applied to README's timer block, applied to a module
reference. ``docs/MODULES-DEPLOYMENT.md`` opens by claiming it covers
"``packages/usa-wa-api/`` (the FastAPI deployment) and the repo-root
directories", then draws one fenced tree. Nothing checked it, and by 84878c8 it
had drifted six ways at once: ``serving/`` drawn as a child of ``api/`` when it
is a sibling, ``api/datasets.py`` (#311) and ``api/serving.py`` (#313) and
``cli/`` absent entirely, six of ten tracked root directories absent, and
``conftest_coverage.py`` missing beside the two siblings that were listed.

An inventory nobody checks reads the same whether it is current or three
refactors behind — and a *misplaced* entry reads worse than an absent one,
because the reader who trusts it looks in a directory that does not exist.

Five things here are cheap to decide against ``git ls-files`` and change
rarely, so pinning them costs no churn:

* every tracked repo-root directory is an entry;
* every subpackage of ``src/usa_wa_api/`` is placed **by its full path from the
  package root** — ``src/usa_wa_api/serving/``, never a bare ``serving/`` under
  some other header. Depth is the half that drifted worst, and a bare leaf name
  cannot express it;
* every module directly inside the package root or one of those subpackages is
  an entry;
* every root-level ``conftest*.py`` is an entry — listing two of the three is
  how ``conftest_coverage.py`` stayed invisible;
* the doc carries exactly one fenced block, so "which block is the tree" is
  never ambiguous.

What is NOT pinned: the prose beside each entry, the route count (API.md is
pinned live by ``tests/test_v1_contract.py``), and anything nested deeper than a
subpackage. Those need judgement or would turn every new helper into a docs
commit.

Pure file parse — no DB, no systemd.
"""

import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DOC = REPO / "docs" / "MODULES-DEPLOYMENT.md"

#: Root directories the tree is not expected to name. ``packages/`` is the
#: doc's subject — every MODULES-*.md describes something under it — rather than
#: an entry in it. Root FILES are not swept at all beyond the conftest family
#: below: pyproject.toml, uv.lock and the dotfile configs are single-purpose and
#: documented where they are configured, not in a module reference.
ROOT_EXEMPT = frozenset({"packages/"})


def _tracked() -> list[str]:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
        ["git", "-c", "core.quotePath=false", "ls-files"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.splitlines()


def _tree_block() -> str:
    """The doc's single fenced code block — the tree this module pins.

    A doc that grew a second fence would make "which block is the tree"
    ambiguous, and silently pinning the wrong one is the failure this whole
    module exists to prevent. So: exactly one, asserted.
    """
    blocks = re.findall(r"^```\n(.*?)^```", DOC.read_text(), re.S | re.M)
    assert len(blocks) == 1, (
        f"expected exactly one fenced tree in {DOC.name}, found {len(blocks)} — "
        "this guard cannot tell which one it should be pinning"
    )
    return blocks[0]


def _entries() -> set[str]:
    """The tree's entry names — the first whitespace-delimited token of a line.

    Exact entries, not a substring sweep of the block. A substring check was
    the first version and it was vacuous for the root half: renaming the
    ``scripts/`` ENTRY still passed, because another entry's prose mentioned
    ``scripts/tests/``. Every check below asks "is there a line FOR this",
    which is the question the doc's reader asks too.

    The first token, NOT the text before the em dash (#373 CR 7). Splitting on
    ``—`` made the separator load-bearing: an author who typed a hyphen got
    ``repo-root directories the tree does not name: ['scripts/']`` about an
    entry sitting right there on the line, and nothing in the doc said an em
    dash was required. Every real entry is one token — ``src/usa_wa_api/api/``,
    ``conftest_coverage.py``, ``usa-wa-api/`` — so the separator stops
    mattering. Continuation lines contribute a junk token that no check looks
    up.
    """
    return {line.split()[0] for line in _tree_block().splitlines() if line.split()}


def test_the_doc_exists_and_draws_one_tree() -> None:
    assert DOC.is_file(), f"missing {DOC}"
    assert _tree_block().strip(), "the fenced tree is empty"


def test_every_tracked_root_directory_is_named() -> None:
    """The doc says "and the repo-root directories"; this makes that true."""
    roots = {f"{f.split('/', 1)[0]}/" for f in _tracked() if "/" in f} - ROOT_EXEMPT
    entries = _entries()
    missing = sorted(root for root in roots if root not in entries)
    assert not missing, (
        f"repo-root directories the tree does not name: {missing} — the doc's "
        "opening line claims to cover them"
    )


def test_every_root_conftest_is_named() -> None:
    """conftest.py, conftest_db.py and conftest_coverage.py are one family.

    Listing two of the three is how ``conftest_coverage.py`` — the #198 unit
    profile and the #216 integration exemption — stayed invisible.
    """
    conftests = sorted(f for f in _tracked() if re.fullmatch(r"conftest\w*\.py", f))
    entries = _entries()
    missing = [name for name in conftests if name not in entries]
    assert not missing, f"root conftest modules the tree does not name: {missing}"


#: ``src/usa_wa_api/`` as git spells it, for slicing tracked paths.
PACKAGE_PREFIX = "packages/usa-wa-api/src/usa_wa_api/"


def _package_paths() -> list[str]:
    """Tracked paths under ``src/usa_wa_api/``, relative to the package root.

    Git, not the filesystem (#373 CR 6). ``iterdir()``/``glob()`` see untracked
    files, so a scratch module — a local experiment, a generated file — made
    this suite demand a doc entry for something git does not track, and
    ``pre-ship.sh`` gates on it, so unrelated work was blocked until the file
    was deleted. The ``__pycache__`` exclusion that version needed was the same
    leak patched one directory at a time; asking git removes the class.
    """
    return [
        path.removeprefix(PACKAGE_PREFIX) for path in _tracked() if path.startswith(PACKAGE_PREFIX)
    ]


def _subpackages() -> list[str]:
    """Every tracked subpackage directly under ``src/usa_wa_api/``."""
    return sorted({path.split("/", 1)[0] for path in _package_paths() if "/" in path})


def test_every_subpackage_is_named_at_its_real_depth() -> None:
    """The worse half of the #373 drift: a path that reads fine and is wrong.

    ``serving/`` was drawn as a child of ``api/`` when it is a sibling, so a
    reader following the tree looked in a directory that does not exist. A bare
    leaf name cannot express depth, so the full path from the package root is
    what the tree has to carry — checked as the literal
    ``src/usa_wa_api/<name>/``.
    """
    entries = _entries()
    missing = [
        f"src/usa_wa_api/{pkg}/"
        for pkg in _subpackages()
        if f"src/usa_wa_api/{pkg}/" not in entries
    ]
    assert not missing, (
        f"subpackages the tree does not place by full path: {missing} — a bare "
        "leaf name under another header puts them at the wrong depth"
    )


def test_every_module_in_the_package_root_and_its_subpackages_is_named() -> None:
    """The absent half: whole route modules missing from a module reference.

    ``api/datasets.py`` (#311, the published-dataset surface) and
    ``api/serving.py`` (#313, ``GET /health/serving``) were both absent, the
    second one easily confused with the ``serving/`` package — which is exactly
    why a reference that names one must name the other.
    """
    entries = _entries()
    missing = sorted(
        path
        for path in _package_paths()
        # 1 segment = the package root, 2 = one level into a subpackage. Deeper
        # is out of scope, as the module docstring says.
        if len(path.split("/")) in (1, 2)
        and path.endswith(".py")
        and not path.endswith("__init__.py")
        and path.rsplit("/", 1)[-1] not in entries
    )
    assert not missing, f"modules the tree does not name: {missing}"


def test_the_doc_names_this_guard() -> None:
    """So the pointer survives a rename, as test_docs_timer_drift requires.

    Derived from ``__file__`` rather than hardcoded (#373 CR 8): a literal
    keeps asserting the OLD name after a rename, which is the failure this test
    claims to prevent. ``test_docs_timer_drift.py`` takes the same route.
    """
    name = Path(__file__).stem
    assert name in DOC.read_text(), (
        f"MODULES-DEPLOYMENT.md must name {name}, the module pinning its tree, "
        "or a rename leaves the doc claiming a guard nobody can find"
    )
