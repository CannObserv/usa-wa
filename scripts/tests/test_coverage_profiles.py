"""The unit tier carries its own coverage gate (#198).

``[tool.coverage.report] fail_under = 80`` measures **all** of ``packages/`` — source
*and* tests. The unit tier (#185) deselects every DB-backed harvester, span builder,
reconciler and route by construction, and with them ~1000 test modules whose bodies then
never execute, so a green unit run scored ~54% and exited non-zero. Every documented
invocation carried ``--no-cov`` to hide that, which cost the tier the one thing a fast
tier is for: it could not be a standalone gate, because a passing run and a failing run
were indistinguishable by exit code unless you remembered the flag.

:file:`conftest_coverage.py` gives the tier a second profile instead of an exemption —
narrower scope, its own floor. These tests pin the properties that make it a ratchet
rather than a rubber stamp:

1. the scope is a **glob** over ``packages/*/src/**``, so a new module is measured the
   day it lands and no hand-maintained list can rot,
2. it covers every package's ``src`` tree and no test module,
3. the floor is below the whole-tree floor but still fires,
4. the pytest-cov attributes the profile retunes still exist,
5. the documented commands no longer carry ``--no-cov``.

Pure file parse, plus one subprocess run of a synthetic project — no DB.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from coverage.files import GlobMatcher, prep_patterns

import conftest_coverage

REPO = Path(__file__).parent.parent.parent  # scripts/tests/ → repo
PYPROJECT = REPO / "pyproject.toml"

#: Files the run-level ``omit`` already drops, so the unit scope never sees them.
OMITTED_PACKAGES = ("powermap-client",)

DOCS_WITH_TEST_COMMANDS = ("AGENTS.md", "README.md", "docs/COMMANDS.md")

#: The marker expression, not the whole command line — the flag this test hunts for sits
#: between ``pytest`` and ``-m``, so matching the command verbatim never matches.
UNIT_TIER_SELECTOR = "-m 'not db and not integration'"


def _ini() -> dict:
    return tomllib.loads(PYPROJECT.read_text())["tool"]["pytest"]["ini_options"]


def _unit_matcher() -> GlobMatcher:
    return GlobMatcher(prep_patterns(_ini()["unit_cov_include"]), "unit_cov_include")


class _FakeConfig:
    """The three attributes the tier test reads."""

    def __init__(self, *, source=pytest.Config.ArgsSource.TESTPATHS, markexpr="not db", keyword=""):
        self.args_source = source
        self.option = type("Opts", (), {"markexpr": markexpr, "keyword": keyword})()


class _FakeItem:
    def __init__(self, *markers: str) -> None:
        self._markers = markers

    def get_closest_marker(self, name: str):
        return name if name in self._markers else None


# --- the scope is derived, not listed -------------------------------------------------


def test_the_unit_scope_is_a_glob() -> None:
    """A hand-maintained module list rots; a glob cannot.

    The failure mode this forecloses is the one #195 and #201 hit elsewhere in this
    repo — a list written once, true once, and never revisited.
    """
    patterns = _ini()["unit_cov_include"]

    assert patterns, "no unit_cov_include configured; the unit tier has no profile"
    assert all("*" in pattern for pattern in patterns), (
        f"unit_cov_include {patterns} names files literally; use a glob so a new "
        "module is in scope the day it lands"
    )


def test_every_package_source_tree_is_in_the_unit_scope() -> None:
    """A package whose layout the glob misses would be silently unmeasured.

    Not "does the glob parse" but "does it still match every tree on disk" — a package
    added with a flat layout, or a rename of ``src``, drops out of the floor's
    denominator and the number goes *up* for the wrong reason.
    """
    matcher = _unit_matcher()
    missed = sorted(
        src.relative_to(REPO).as_posix()
        for src in REPO.glob("packages/*/src")
        if src.parent.name not in OMITTED_PACKAGES
        and not any(matcher.match(str(module)) for module in src.rglob("*.py"))
    )

    assert missed == [], (
        f"{missed} hold Python sources that unit_cov_include does not match; "
        "they are excluded from the unit tier's floor without saying so"
    )


def test_no_test_module_is_in_the_unit_scope() -> None:
    """Scoping to ``src`` is the whole point.

    ~1000 test modules are deselected by the tier, so their bodies score 0 and dominate
    an unscoped total. Measuring them tells you which tests ran, not how much shipping
    code the tier exercises.
    """
    matcher = _unit_matcher()
    leaked = sorted(
        path.relative_to(REPO).as_posix()
        for path in REPO.glob("packages/*/tests/**/*.py")
        if matcher.match(str(path))
    )

    assert leaked == [], f"unit_cov_include matches test modules {leaked}"


# --- the floors ------------------------------------------------------------------------


def test_the_whole_tree_floor_is_unchanged() -> None:
    """The unit profile is an addition. The full run's gate must not soften."""
    report = tomllib.loads(PYPROJECT.read_text())["tool"]["coverage"]["report"]

    assert report["fail_under"] == 80


def test_the_unit_floor_is_below_the_whole_tree_floor() -> None:
    """A unit floor at or above 80 would be aspirational, not a ratchet — it would fail
    on a green tree and get switched off within a week."""
    unit = float(_ini()["unit_cov_fail_under"])
    whole_tree = tomllib.loads(PYPROJECT.read_text())["tool"]["coverage"]["report"]["fail_under"]

    assert 0 < unit < whole_tree


# --- tier detection --------------------------------------------------------------------


def test_a_run_with_no_db_or_integration_items_is_the_unit_tier() -> None:
    assert conftest_coverage.selects_only_unit_tests(
        _FakeConfig(), [_FakeItem(), _FakeItem("slow")]
    )


@pytest.mark.parametrize("marker", ["db", "integration"])
def test_a_run_holding_a_database_item_is_not_the_unit_tier(marker: str) -> None:
    """``-m`` is last-wins against ``addopts``, so ``-m 'not db'`` alone re-selects the
    integration tier. Deciding on the *selected items* rather than on the text of the
    marker expression cannot be fooled by that, or by whitespace, or by ordering."""
    assert not conftest_coverage.selects_only_unit_tests(
        _FakeConfig(), [_FakeItem(), _FakeItem(marker)]
    )


def test_a_subset_run_is_not_the_unit_tier() -> None:
    """``pytest packages/usa-wa-api/tests/test_health.py`` selects no DB test either, and
    a single file cannot clear a whole-tree floor. Path args and ``-k`` both mean "a
    slice", so neither gets the tier's gate — they keep ``--no-cov``."""
    assert not conftest_coverage.selects_only_unit_tests(
        _FakeConfig(source=pytest.Config.ArgsSource.ARGS), [_FakeItem()]
    )
    assert not conftest_coverage.selects_only_unit_tests(
        _FakeConfig(keyword="health"), [_FakeItem()]
    )


def test_an_unfiltered_run_is_not_the_unit_tier() -> None:
    """Bare ``pytest`` on a box with no test database deselects nothing, yet collects
    zero DB items only because collection failed. No marker expression, no tier."""
    assert not conftest_coverage.selects_only_unit_tests(_FakeConfig(markexpr=""), [_FakeItem()])


def test_an_empty_selection_is_not_the_unit_tier() -> None:
    """``-m 'not db and not integration' -k nothing-matches`` would otherwise retune the
    profile and then fail on 0%."""
    assert not conftest_coverage.selects_only_unit_tests(_FakeConfig(), [])


# --- the pytest-cov seam ---------------------------------------------------------------


def test_the_attributes_the_profile_retunes_still_exist(pytestconfig: pytest.Config) -> None:
    """The profile reaches into pytest-cov: the plugin registered as ``_cov``, its
    ``options.cov_fail_under``, and the live ``Coverage`` object's ``report_include``.

    None of that is a published API. A rename upstream fails safe — the whole-tree floor
    stays in place and the tier goes red on 54% — but red with no explanation, on a run
    that changed nothing. This names the cause.
    """
    plugin = pytestconfig.pluginmanager.get_plugin(conftest_coverage.COV_PLUGIN_NAME)
    if plugin is None or getattr(plugin, "cov_controller", None) is None:
        pytest.skip("run without coverage (--no-cov)")

    assert hasattr(plugin.options, "cov_fail_under")
    assert hasattr(plugin.cov_controller.cov.config, "report_include")


# --- the gate actually fires -----------------------------------------------------------


SYNTHETIC_PYPROJECT = """
[tool.pytest.ini_options]
testpaths = ["pkg"]
addopts = "-m 'not integration' --cov=pkg --cov-report=term"
markers = ["db: db", "integration: integration"]
unit_cov_include = ["pkg/*/src/**"]
unit_cov_fail_under = "{unit_floor}"

[tool.coverage.report]
fail_under = {whole_tree_floor}
"""

SYNTHETIC_SOURCE = """
def covered() -> int:
    return 1


def uncovered() -> int:
    total = 0
    for _ in range(3):
        total += 1
    return total
"""

SYNTHETIC_TESTS = """
import pytest

from shipped import covered


def test_covered():
    assert covered() == 1


@pytest.mark.db
def test_needs_a_database():
    raise AssertionError("deselected by the unit tier")
"""


def _synthetic_run(
    tmp_path: Path,
    *extra_args: str,
    unit_floor: str = "90",
    whole_tree_floor: int = 0,
) -> subprocess.CompletedProcess[str]:
    """Build the synthetic project and run its unit tier in a subprocess.

    The project mirrors this one's shape — sources *and* tests under the measured root —
    so the scope swap is observable. ``whole_tree_floor`` defaults to 0 so the unit
    profile is normally the only thing that can fail the run; raise it to observe the
    floor that stands when the profile declines to apply.
    """
    (tmp_path / "pyproject.toml").write_text(
        SYNTHETIC_PYPROJECT.format(unit_floor=unit_floor, whole_tree_floor=whole_tree_floor)
    )
    (tmp_path / "conftest.py").write_text('pytest_plugins = ("conftest_coverage",)\n')
    src = tmp_path / "pkg" / "shipped" / "src"
    src.mkdir(parents=True)
    (src / "shipped.py").write_text(SYNTHETIC_SOURCE)
    tests = tmp_path / "pkg" / "shipped" / "tests"
    tests.mkdir()
    (tests / "test_shipped.py").write_text(SYNTHETIC_TESTS)

    env = {
        key: value
        for key, value in os.environ.items()
        # pytest-cov exports COV_CORE_* so subprocesses join the *parent's* measurement,
        # and PYTEST_ADDOPTS would leak this run's flags into the child.
        if not key.startswith(("COV_CORE_", "PYTEST_"))
    }
    env["PYTHONPATH"] = os.pathsep.join([str(REPO), str(src)])

    return subprocess.run(
        # -q so the only place a filename can appear is the coverage report itself.
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-m",
            "not db and not integration",
            "-p",
            "no:cacheprovider",
            *extra_args,
        ],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_the_unit_floor_fails_a_run_that_is_under_it(tmp_path: Path) -> None:
    """A gate that never fires is an exemption wearing a number.

    Proved on a synthetic tree rather than this one: here the two directions cannot both
    be shown, because *this* test runs inside the tier whose exit code is the other
    direction.
    """
    result = _synthetic_run(tmp_path)

    assert result.returncode != 0, f"the unit floor did not fire:\n{result.stdout}"
    assert "Required test coverage of 90" in result.stdout, result.stdout
    assert "1 passed" in result.stdout, "the run failed for some reason other than coverage"
    # And on the scoped total, not the whole tree: the test module is measured (it ran)
    # but must not appear in a report scoped to `pkg/*/src/**`. Without this the floor
    # fires either way and the scope goes unproven — which is exactly how it shipped
    # broken the first time, with `Central.finish()` swapping in an unscoped reporter.
    assert "test_shipped.py" not in result.stdout, result.stdout
    assert "src/shipped.py" in result.stdout, result.stdout


@pytest.mark.parametrize("spelling", ["--cov-fail-under=95", "--cov-fail-under 95"])
def test_an_explicit_floor_on_the_command_line_wins(tmp_path: Path, spelling: str) -> None:
    """The profile must never swap a weaker floor in for one the operator asked for.

    Before this guard, ``pytest -m 'not db and not integration' --cov-fail-under=90``
    exited **0** at 65% on this repo: the profile overwrote 90 with its own 64 and
    announced the operator's own flag back to them as the whole-tree gate. Someone raising
    the bar in CI got it silently lowered — the exact "a green run and a red run look the
    same" failure #198 exists to end, pointing the other way.

    Both argparse spellings, because matching only ``--flag=value`` would leave the
    space-separated form silently overridden.
    """
    result = _synthetic_run(tmp_path, *spelling.split(" "))

    assert "NOT applied" in result.stdout, (
        f"the profile applied anyway, overriding {spelling}:\n{result.stdout}"
    )
    assert "Required test coverage of 95" in result.stdout, result.stdout
    assert "Required test coverage of 90" not in result.stdout, (
        f"the unit floor replaced the operator's:\n{result.stdout}"
    )


def test_an_explicit_cov_scope_on_the_command_line_wins(tmp_path: Path) -> None:
    """Same guard for the other half. ``--cov=<subset>`` is an explicit scope, and the
    profile's ``report_include`` would otherwise clobber it — measuring something the
    operator did not ask about, then gating on it."""
    result = _synthetic_run(tmp_path, "--cov=pkg/shipped/src")

    assert "NOT applied" in result.stdout, result.stdout


def test_a_malformed_unit_floor_leaves_the_whole_tree_floor_standing(tmp_path: Path) -> None:
    """This module's contract is that anything stopping the retune leaves the whole-tree
    floor in place, so the tier fails loudly rather than passing on nothing.

    ``float()`` on a typo'd ini value used to raise straight out of the collection hook —
    the one path that honoured neither half of that promise. Whole-tree floor set high
    here so "the floor still stands" is observable rather than merely asserted.
    """
    result = _synthetic_run(tmp_path, unit_floor="sixty-four", whole_tree_floor=99)

    assert "NOT applied" in result.stdout, result.stdout
    assert "not a number" in result.stdout, result.stdout
    assert "Required test coverage of 99" in result.stdout, result.stdout
    assert "Traceback" not in result.stderr, f"the hook raised instead:\n{result.stderr}"


# --- the documented commands -----------------------------------------------------------


@pytest.mark.parametrize("doc", DOCS_WITH_TEST_COMMANDS)
def test_the_documented_unit_tier_command_carries_no_flag(doc: str) -> None:
    """The point of the profile: the tier is runnable, and gated, as written.

    A ``--no-cov`` that creeps back into the documented command restores the failure
    mode — a reader copies the flag, and the tier stops being a gate again.
    """
    lines = [
        line
        for line in (REPO / doc).read_text().splitlines()
        if UNIT_TIER_SELECTOR in line and "--no-cov" in line
    ]

    assert lines == [], f"{doc} documents the unit tier with --no-cov: {lines}"
