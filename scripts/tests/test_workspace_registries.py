"""Assert every workspace package is registered in all three root-pyproject lists (#187).

`[tool.uv.workspace] members = ["packages/*"]` is a glob, so a new package is
picked up by `uv sync` without appearing in any of the hand-maintained registries
that every other package appears in:

- `[dependency-groups] dev` — the editable-install list
- `[tool.uv.sources]` — the workspace-resolution list
- `[tool.ruff.lint.isort] known-first-party` — the import-grouping list

That is how `usa-wa-adapter-sos` — the module behind three production systemd
units — ended up in none of them (AR-10, finding 10): nothing broke, so nothing
noticed. Its imports were lint-classified as third-party and its dependency edge
rested entirely on glob behaviour. `usa_wa_adapter_pdc` and `powermap_client`
(deleted in #314) were likewise missing from `known-first-party`.

This guard closes that gap the way `test_unit_ordering` closes the
systemd-ordering gap: the on-disk package set is cross-checked against each
registry, so adding a package that skips any list fails the suite.

A second guard (#572) holds the same declarations to a version policy: every
dependency that carries a specifier must also carry an upper bound (by
convention `<N+1`, or `<1` for a 0.x release), so a breaking major can't arrive through a plain
`uv lock --upgrade` or a Dependabot group with nothing in `pyproject.toml`
marking it. vcrpy 8 → 9 rode in that way under #569.

Pure file parse — no DB, no `uv`; runs everywhere.
"""

import re
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).parent.parent.parent  # scripts/tests/ → repo
PYPROJECT = REPO / "pyproject.toml"
PACKAGES = REPO / "packages"

# A PEP 508 requirement carries a version specifier, extra, or marker; a bare
# workspace member does not. That is the only signal left after tomllib drops
# the "# Workspace members" / "# Tools" comments, and it is the right one: a
# workspace member is pinned by `[tool.uv.sources]`, never by a specifier.
_SPECIFIER = re.compile(r"[<>=!~;\[\s@]")

# ruff/isort names modules, not distributions: `usa-wa-adapter-sos` imports as
# `usa_wa_adapter_sos`. Every package in this workspace uses the src-layout
# dash↔underscore convention, asserted below so a package that breaks it can't
# make this normalisation silently wrong.
MODULE_NAME_EXCEPTIONS: dict[str, str] = {}

# PEP 440 operators that bound a version from above (#572). `~=X.Y` admits only
# X.*, and `==` / `===` admit one version (or one prefix, with `.*`), so each is
# a cap. `>`, `>=` and `!=` are not. A direct reference counts as uncapped (a git
# URL can float), and so does any clause this parse can't read: the guard errs
# red, never green.
_UPPER_BOUND_OPERATORS = frozenset({"<", "<=", "==", "===", "~="})
_CLAUSE_OPERATOR = re.compile(r"^\s*(===|~=|==|!=|<=|>=|<|>)")
_NAME_AND_EXTRAS = re.compile(r"^\s*[A-Za-z0-9][A-Za-z0-9._-]*\s*(\[[^\]]*\])?")

# How many specified (non-member) entries the cap guard inspects, summed across
# the root `[dependency-groups]` and every member's `[project].dependencies`. A
# parse that silently sees nothing would otherwise pass. Adding or removing a
# dependency moves it — update it in the same change.
EXPECTED_SPECIFIED_ENTRIES = 43


def _config() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def _package_dirs() -> set[str]:
    """Directory names under `packages/` that are actual workspace members."""
    return {p.name for p in PACKAGES.iterdir() if (p / "pyproject.toml").is_file()}


def _dev_workspace_entries() -> set[str]:
    """`[dependency-groups] dev` entries that are workspace members, not tools."""
    dev = _config()["dependency-groups"]["dev"]
    return {entry for entry in dev if _SPECIFIER.search(entry) is None}


def _uv_source_keys() -> set[str]:
    return set(_config()["tool"]["uv"]["sources"])


def _known_first_party() -> set[str]:
    return set(_config()["tool"]["ruff"]["lint"]["isort"]["known-first-party"])


def _specified_entries() -> list[tuple[str, str]]:
    """Every non-member dependency, labelled with where it is declared.

    Root `[dependency-groups]` (every group) plus each member's
    `[project].dependencies`; `_SPECIFIER` drops the bare workspace members.
    """
    entries = [
        (f"pyproject.toml [dependency-groups] {group}", entry)
        for group, group_entries in _config()["dependency-groups"].items()
        for entry in group_entries
        if _SPECIFIER.search(entry)
    ]
    for package in sorted(_package_dirs()):
        project = tomllib.loads((PACKAGES / package / "pyproject.toml").read_text())["project"]
        entries += [
            (f"packages/{package}/pyproject.toml", entry)
            for entry in project.get("dependencies", [])
            if _SPECIFIER.search(entry)
        ]
    return entries


def _has_upper_bound(entry: str) -> bool:
    """Whether a PEP 508 requirement caps its version from above."""
    requirement = entry.split(";", 1)[0]
    if "@" in requirement:
        return False
    specifier = _NAME_AND_EXTRAS.sub("", requirement, count=1).strip().strip("()")
    operators = {
        match.group(1)
        for clause in specifier.split(",")
        if (match := _CLAUSE_OPERATOR.match(clause))
    }
    return bool(operators & _UPPER_BOUND_OPERATORS)


def _module_name(package: str) -> str:
    return MODULE_NAME_EXCEPTIONS.get(package, package.replace("-", "_"))


def test_every_package_is_a_dev_dependency():
    """A new package must be installed editable by a plain `uv sync`."""
    assert _dev_workspace_entries() == _package_dirs()


def test_every_package_is_a_uv_source():
    """A new package must resolve from the workspace, not from an index."""
    assert _uv_source_keys() == _package_dirs()


def test_every_package_is_known_first_party():
    """A new package's imports must sort as first-party, not third-party."""
    assert _known_first_party() == {_module_name(p) for p in _package_dirs()}


@pytest.mark.parametrize("package", sorted(_package_dirs()))
def test_package_dir_name_matches_distribution_name(package):
    """The `packages/<dir>` name is the distribution name the registries key on.

    Without this the three set comparisons above would pass while a registry
    entry named nothing real sat in the lists — the directory scan is only a
    trustworthy source of truth if the directory *is* the package.
    """
    config = tomllib.loads((PACKAGES / package / "pyproject.toml").read_text())
    assert config["project"]["name"] == package


@pytest.mark.parametrize("package", sorted(_package_dirs()))
def test_module_name_normalisation_is_real(package):
    """The dash→underscore module name is importable on disk.

    Proves `_module_name` describes reality rather than a naming convention that
    has quietly drifted, which would make `test_every_package_is_known_first_party`
    assert the wrong set.
    """
    module = _module_name(package)
    root = PACKAGES / package
    assert (root / "src" / module).is_dir() or (root / module).is_dir()


def test_specifier_split_separates_members_from_tools():
    """Proof the classifier has teeth, using the same regex as the production path.

    Bare names are workspace members; anything carrying a specifier, extra,
    marker, or direct reference is a tool. A wrong edit to `_SPECIFIER` fails
    here too.
    """
    bare = {"usa-wa-adapter-sos", "clearinghouse_core"}
    pinned = {
        "pytest>=8.0,<9",
        "ruff<1",
        "vcrpy!=7.1",
        "httpx~=0.27",
        "uvicorn[standard]",
        "anyio; python_version >= '3.12'",
        "foo @ https://example.invalid/foo.whl",
    }
    assert {e for e in bare | pinned if _SPECIFIER.search(e) is None} == bare


def test_every_specified_dependency_carries_an_upper_bound():
    """A specifier without an upper bound lets a breaking major in unannounced (#572).

    The guard checks that a bound exists, not how wide it is: the convention is
    `<N+1` for the locked major N, and `<1` for 0.x (owner decision, 2026-11-06;
    the tighter `<0.(N+1)` would rewrite `pyproject.toml` on every minor).
    """
    uncapped = [
        f"{source}: {entry}"
        for source, entry in _specified_entries()
        if not _has_upper_bound(entry)
    ]
    assert not uncapped, "no major-version cap: " + "; ".join(uncapped)


def test_cap_guard_inspects_every_specified_entry():
    """Pins the inspected count, so a parse that finds nothing can't pass the guard."""
    assert len(_specified_entries()) == EXPECTED_SPECIFIED_ENTRIES


def test_upper_bound_detection_has_teeth():
    """`<`, `<=`, `==`, `===` and `~=` cap.

    `>`, `>=`, `!=`, a bare name and a direct reference don't.
    """
    capped = {
        "vcrpy>=9.2.0,<10",
        "ruff<1",
        "pytest <= 9.9",
        "zeep==4.3.3",
        "zeep==4.*",
        "zeep===4.3.3",
        "httpx~=0.27",
        "uvicorn[standard]>=0.34.0,<1",
        "anyio>=4,<5; python_version >= '3.12'",
        "bar (>=1,<2)",
    }
    uncapped = {
        "vcrpy>=9.2.0",
        "zeep>4",
        "pdfplumber!=0.11.5",
        "uvicorn[standard]",
        "anyio; python_version < '4'",
        "foo @ git+https://example.invalid/foo.git@main",
        "bar (>=1)",
    }
    assert {e for e in capped | uncapped if _has_upper_bound(e)} == capped
