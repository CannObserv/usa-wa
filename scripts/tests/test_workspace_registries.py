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
were likewise missing from `known-first-party`.

This guard closes that gap the way `test_unit_ordering` closes the
systemd-ordering gap: the on-disk package set is cross-checked against each
registry, so adding a package that skips any list fails the suite.

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
