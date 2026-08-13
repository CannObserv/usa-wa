"""A second coverage profile, for the unit tier (#198).

``[tool.coverage.report] fail_under = 80`` measures **all** of ``packages/`` — source
*and* tests — which is right for ``pytest`` and wrong for ``pytest -m 'not db and not
integration'``. The unit tier (#185) deselects every DB-backed harvester, span builder,
reconciler and route by construction, and with them ~1000 test modules whose bodies then
score zero, so a *green* tier totalled ~54% and exited non-zero. The documented
invocations all carried ``--no-cov``, which cost the tier the point of being fast: a
gate you have to remember a flag for is not a gate.

So the tier gets its own profile rather than an exemption:

* **scope** — ``unit_cov_include``, a glob over ``packages/*/src/**``. Shipping code
  only. A list of modules would be true the day it was written and rot from there
  (the failure mode of #195's stale cardinalities and #201's false comment); a glob
  measures a new module the day it lands, and ``test_coverage_profiles.py`` checks it
  still matches every package's tree.
* **floor** — ``unit_cov_fail_under``. Measured, not chosen: see the pyproject comment.

The whole-tree profile is untouched, so ``pytest`` still gates on 80% of everything.

Detection is by *selection*, not by the text of ``-m``: a run is the unit tier when it
collected the full testpaths, filtered only by a marker expression, and ended up holding
no ``db`` and no ``integration`` item. That is immune to whitespace, to argument order,
and to ``-m 'not db'`` silently re-selecting the integration tier (``-m`` is last-wins
against ``addopts``). A path-arg or ``-k`` run is a *slice* — it cannot clear a
whole-tree floor either, so it is left alone and keeps ``--no-cov``.

The **integration tier** (#216) gets the opposite treatment — an exemption, not a
second floor. Its e2e test drives the daily refresh through ``subprocess.run``, so the
child process's lines are never measured: a line-coverage number for this tier gates
nothing real, and measuring nothing is more honest than gating on the wrong thing
(the asymmetry with #198: the unit tier runs in-process, so its number means
something). Detection mirrors the unit tier's — by *selection*, not the text of
``-m``: a run is the integration tier when it collected the full testpaths, filtered
only by a marker expression, and **every** selected item carries ``integration``.
True for ``-m integration`` and narrowings like ``-m 'integration and not slow'``;
never for a mixed selection such as ``-m 'not db'`` (last-wins against addopts, it
re-includes the integration tests *beside* the unit ones), which keeps the whole-tree
floor. The waive zeroes ``cov_fail_under`` — the report still prints, the gate is
gone — and an explicit ``--cov``/``--cov-fail-under`` on the command line still wins,
exactly as for the unit profile.

Retuning happens at ``pytest_collection_finish``: after deselection, before
pytest-cov reads ``cov_fail_under`` at the end of ``pytest_runtestloop``.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import coverage
import pytest

#: The name pytest-cov registers its plugin under. Not a published API — nor are
#: ``options.cov_fail_under`` and ``cov_controller.cov.config.report_include`` — so
#: ``test_coverage_profiles.py`` asserts all three still exist.
#:
#: Failing to retune is *safe by construction*: every early return here leaves the
#: whole-tree floor in place, and the unit tier then fails loudly on 54% rather than
#: passing on nothing. The gate can only get stricter, never silently vanish. The test
#: exists to name the cause, not to prevent silence.
COV_PLUGIN_NAME = "_cov"

#: Selecting one of these means the run is not the DB-free tier.
NON_UNIT_MARKERS = ("db", "integration")

#: The marker whose tier is exempt from the coverage floor (#216).
INTEGRATION_MARKER = "integration"


def pytest_addoption(parser: pytest.Parser) -> None:
    """Both knobs live in ``pyproject.toml``, beside the coverage config they qualify."""
    parser.addini(
        "unit_cov_include",
        "Coverage report scope for the unit tier (glob patterns).",
        type="args",
        default=[],
    )
    parser.addini(
        "unit_cov_fail_under",
        "Coverage floor for the unit tier, over unit_cov_include.",
        default="",
    )


def marker_only_run(config: Any, items: Sequence[Any]) -> bool:
    """A non-empty selection of the full testpaths, filtered only by a marker expression.

    A path-arg or ``-k`` run is a *slice*; a bare run has no marker expression; an empty
    selection is nothing at all. None of those is a tier.
    """
    if not items:
        return False
    if config.args_source is not pytest.Config.ArgsSource.TESTPATHS:
        return False
    return bool(config.option.markexpr) and not config.option.keyword


def selects_only_unit_tests(config: Any, items: Sequence[Any]) -> bool:
    """Is this run the unit tier — the whole workspace, minus everything needing a DB?"""
    if not marker_only_run(config, items):
        return False
    return not any(item.get_closest_marker(marker) for item in items for marker in NON_UNIT_MARKERS)


def selects_only_integration_tests(config: Any, items: Sequence[Any]) -> bool:
    """Is this run the integration tier — nothing but ``integration``-marked items?

    Decided on the selected items, like the unit tier: ``-m integration`` and any
    narrowing of it qualify; a mixed selection (``-m 'not db'`` re-includes integration
    tests beside unit ones) does not, and keeps the whole-tree floor.
    """
    if not marker_only_run(config, items):
        return False
    return all(item.get_closest_marker(INTEGRATION_MARKER) for item in items)


def live_coverage(cov_controller: Any) -> list[coverage.Coverage]:
    """Every ``Coverage`` object the controller holds.

    Not ``cov_controller.cov``: ``Central.finish()`` reassigns that attribute from a
    *second* object built at start-up (``combining_cov``, which loads and combines the
    data files), and it is the second one that reports. Scoping only the first left the
    floor applied to an unscoped total — a gate on the wrong number, which is worse than
    no gate. Discovered by type rather than by name so a third object, or a rename,
    cannot reintroduce that.
    """
    return [
        value for value in vars(cov_controller).values() if isinstance(value, coverage.Coverage)
    ]


def cli_named(config: pytest.Config, *flags: str) -> str | None:
    """The first of ``flags`` the operator put on the command line, or ``None``.

    ``invocation_params.args`` is the operator's own argv, and deliberately **excludes**
    ``addopts`` — which is where this project already sets ``--cov=packages``. Consulting
    addopts as well would mean the unit profile never applied to anything.

    Matches both spellings argparse accepts: ``--flag value`` and ``--flag=value``.
    """
    for arg in config.invocation_params.args:
        for flag in flags:
            if arg == flag or arg.startswith(f"{flag}="):
                return flag
    return None


def retune_coverage(config: pytest.Config) -> str | None:
    """Point the live ``Coverage`` objects at the unit scope and floor.

    Returns the line to announce, or ``None`` when there is nothing to say — a
    ``--no-cov`` run, or a checkout that configured neither knob.
    """
    plugin = config.pluginmanager.get_plugin(COV_PLUGIN_NAME)
    if plugin is None or getattr(plugin, "cov_controller", None) is None:
        return None
    if plugin.options.no_cov:
        return None

    include = list(config.getini("unit_cov_include"))
    fail_under = config.getini("unit_cov_fail_under")
    if not include or not fail_under:
        return None

    # The operator set the coverage gate by hand on this run, so leave it exactly as
    # asked (#198 CR-16). Swapping a *weaker* floor in for an explicitly requested
    # stronger one is the precise failure this profile exists to end — `--cov-fail-under=90`
    # was passing at 65% before this guard. Bailing out entirely, rather than honouring
    # their floor over our narrower scope, keeps the whole-tree floor: the stricter of the
    # two, and the one they were already reasoning about when they typed the flag.
    manual = cli_named(config, "--cov-fail-under", "--cov")
    if manual is not None:
        return f"unit tier: coverage profile NOT applied — {manual} given on the command line"

    reporters = live_coverage(plugin.cov_controller)
    if not reporters:
        return None
    try:
        floor = float(fail_under)
    except ValueError:
        # Consistent with this module's contract: anything that stops us retuning leaves
        # the whole-tree floor standing, so a typo makes the tier fail loudly on 54%
        # rather than raising out of a collection hook (#198 CR-19).
        return (
            f"unit tier: coverage profile NOT applied — unit_cov_fail_under="
            f"{fail_under!r} is not a number (the whole-tree floor stands)"
        )

    # The whole-tree number comes from the coverage config, NOT from
    # `plugin.options.cov_fail_under`: the latter is the *effective* value, so quoting it
    # would echo the operator's own `--cov-fail-under` back at them as though it were the
    # configured gate (#198 CR-16). Quoting the real one is the point of the line — a
    # reader who sees "Required test coverage of 64%" needs to know 80% did not evaporate.
    whole_tree = reporters[0].config.fail_under
    for cov in reporters:
        cov.config.report_include = include
    plugin.options.cov_fail_under = floor
    replaced = "no floor" if not whole_tree else f">={whole_tree:g}%"
    return (
        f"unit tier: coverage scoped to {' '.join(include)} at >={fail_under}% "
        f"(the full run gates everything measured at {replaced})"
    )


def waive_coverage_floor(config: pytest.Config) -> str | None:
    """Zero the coverage floor for the integration tier (#216).

    The tier's headline test exercises the refresh via ``subprocess.run``, so its lines
    are invisible to coverage — no floor over this run gates anything real. The report
    still prints (zeroing ``cov_fail_under`` removes the gate, not the measurement);
    suppressing measurement outright would mean reaching deeper into pytest-cov than the
    ``options.cov_fail_under`` seam #198 already leans on.

    Returns the line to announce, or ``None`` when there is nothing to say — a
    ``--no-cov`` run, or no floor standing in the first place.
    """
    plugin = config.pluginmanager.get_plugin(COV_PLUGIN_NAME)
    if plugin is None or getattr(plugin, "cov_controller", None) is None:
        return None
    if plugin.options.no_cov:
        return None

    # Same rule as the unit profile (#198 CR-16): an operator who set the coverage gate
    # by hand on this run gets exactly the gate they asked about.
    manual = cli_named(config, "--cov-fail-under", "--cov")
    if manual is not None:
        return f"integration tier: coverage floor NOT waived — {manual} given on the command line"

    # The *effective* floor. No CLI flag reached this line, so it is the configured one.
    floor = plugin.options.cov_fail_under
    plugin.options.cov_fail_under = 0
    if not floor:
        return None
    return (
        f"integration tier: coverage floor waived (whole-tree >={floor:g}% does not apply) — "
        "this tier drives child processes whose lines coverage cannot see (#216)"
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    """Swap profiles once the selection is final."""
    if selects_only_unit_tests(session.config, session.items):
        announcement = retune_coverage(session.config)
    elif selects_only_integration_tests(session.config, session.items):
        announcement = waive_coverage_floor(session.config)
    else:
        return
    if announcement is None:
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(announcement)
