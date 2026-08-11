"""Every unit's ``ExecStart`` target must still exist (CR #196 finding 33).

``verify-units.sh`` (#51) runs ``systemd-analyze verify``, which resolves the ExecStart
**binary** — ``/usr/local/bin/uv`` — and stops there. The rest of the command line is
opaque to it, so the ``python -m <module>`` argument naming what the unit actually runs
has never been checked by anything.

#189 renamed four of those module paths by hand when the seat applications moved to
``usa_wa_facts_seats``. Nothing in the suite would have caught a fifth that was missed:
the tests are green either way, and the failure surfaces at the timer's next elapse as
an ``OnFailure=`` email — 06:00 UTC, hours later, from a unit that no longer exists as
written.

That risk is about to grow. #183 restructures 63 adapter modules and #179b moves ~46
CLIs onto the shared job harness, so entry-point paths are the thing most likely to move
in the next two batches. This is the guard for that whole class.

Static parse plus ``find_spec`` — no DB, no subprocess, no systemd. ``find_spec`` imports
a target's *parent packages* only, never the entry-point module itself, so a CLI's
``_main`` never runs here.
"""

from __future__ import annotations

import importlib.util
import shlex
from pathlib import Path

import pytest
from systemd_units import DEPLOY, unit_values

REPO = Path(DEPLOY).parent

#: Units that run a project shell script hardcode the **production** checkout
#: (``/home/exedev/usa-wa/scripts/...``) because that is where systemd runs them from. A
#: worktree cannot resolve that path, so the check is on the repo-relative tail instead.
SCRIPT_DIR_NAME = "scripts"


def _service_files() -> list[Path]:
    return sorted(DEPLOY.glob("*.service"))


def _exec_starts() -> list[tuple[str, str]]:
    """``(unit_name, command)`` for every ExecStart in every unit."""
    return [
        (path.name, command)
        for path in _service_files()
        for command in unit_values(path, "Service", "ExecStart")
        if command
    ]


def _module_target(command: str) -> str | None:
    """The dotted module a command runs, or ``None`` if it runs no Python module.

    Handles both shapes the deploy tree uses: ``python -m pkg.mod`` and uvicorn's
    ``pkg.mod:attr`` application reference.
    """
    tokens = shlex.split(command)
    if "-m" in tokens:
        index = tokens.index("-m")
        if index + 1 < len(tokens):
            return tokens[index + 1]
    for token in tokens:
        # uvicorn's app reference; the ASGI callable is checked separately below.
        if ":" in token and "/" not in token and token.count(":") == 1:
            module, _, attr = token.partition(":")
            if module and attr and all(part.isidentifier() for part in module.split(".")):
                return module
    return None


def _resolves(module: str) -> bool:
    """Whether ``python -m module`` could find something to run.

    ``find_spec`` signals absence two different ways — ``None`` when the leaf is missing
    but its parent package resolves, ``ModuleNotFoundError`` when a parent does not — and
    a guard that handles only one of them passes on half the failures it exists for.
    """
    try:
        return importlib.util.find_spec(module) is not None
    except ModuleNotFoundError:
        return False


def test_the_deploy_tree_still_has_units_to_check() -> None:
    """A guard over an empty glob passes for the wrong reason."""
    assert len(_exec_starts()) >= 10, "deploy/*.service ExecStart parse found almost nothing"


@pytest.mark.parametrize(
    ("unit", "command"),
    [pytest.param(u, c, id=u) for u, c in _exec_starts()],
)
def test_every_exec_start_target_resolves(unit: str, command: str) -> None:
    """A unit either runs an importable module or an executable script in this repo."""
    module = _module_target(command)
    if module is not None:
        assert _resolves(module), (
            f"{unit} runs `python -m {module}`, which does not resolve — the module was "
            "renamed or moved without updating the unit. systemd-analyze cannot see this: "
            "it validates the ExecStart binary, not its arguments."
        )
        return

    binary = Path(shlex.split(command)[0])
    assert binary.parent.name == SCRIPT_DIR_NAME, (
        f"{unit} runs {binary}, which is neither a Python module nor a repo script — "
        "extend this guard rather than leaving the target unchecked"
    )
    local = REPO / SCRIPT_DIR_NAME / binary.name
    assert local.is_file(), (
        f"{unit} runs {binary}, and {binary.name} does not exist in this checkout's scripts/"
    )
    assert local.stat().st_mode & 0o111, f"{unit} runs {binary}, which is not executable"


#: The #201 archive/rebuild split, pinned per unit: the archive half runs an **adapter** Phase-A
#: driver, the rebuild half a **facts** module. Both directions matter. A rebuild unit pointed
#: back at a module that sources would restore the `import-linter` exception #201 deleted, and an
#: archive unit pointed at a fact would put a live transport back in `usa-wa-facts-seats`. The
#: list-equality also pins ONE ExecStart per unit: chaining the two halves as two ExecStart= lines
#: in one unit was the rejected alternative (a harvest failure would then cancel the rebuild and
#: both halves would share one ledger identity).
SPLIT_TARGETS = {
    "usa-wa-sos-archive-refresh.service": "usa_wa_adapter_sos.results.archive_refresh",
    "usa-wa-pdc-archive-refresh.service": "usa_wa_adapter_pdc.archive_refresh",
    "usa-wa-sos-refresh.service": "usa_wa_facts_seats.house.refresh",
    "usa-wa-pdc-refresh.service": "usa_wa_facts_seats.pdc.refresh",
}


@pytest.mark.parametrize(
    ("unit", "module"), [pytest.param(u, m, id=u) for u, m in sorted(SPLIT_TARGETS.items())]
)
def test_the_archive_rebuild_split_stays_on_its_units(unit: str, module: str) -> None:
    """Each half of the #201 split runs the module its layer owns, one ExecStart apiece."""
    assert [_module_target(command) for u, command in _exec_starts() if u == unit] == [module]


def test_the_guard_rejects_a_renamed_module() -> None:
    """The assertion has teeth.

    Probing the tree for a *specific* stale path would be testing filesystem residue: a
    ``git mv`` leaves the emptied source directory behind locally, git does not track empty
    directories, and ``find_spec`` happily resolves such a leftover as a namespace package.
    So exercise the resolution the parametrised test performs, on a module that cannot
    exist under any residue.
    """
    # Both absence shapes: a missing leaf under a package that still resolves, and a
    # module whose whole parent chain is gone.
    assert not _resolves("usa_wa_adapter_sos.house.refresh")
    assert not _resolves("usa_wa_adapter_sos.nonexistent.refresh")


def test_uvicorn_app_references_name_a_real_callable() -> None:
    """The API unit's ``module:attr`` reference — ``find_spec`` proves the module, not the
    attribute, and uvicorn fails on a missing attribute just as hard as on a missing module."""
    references = [
        token
        for _unit, command in _exec_starts()
        for token in shlex.split(command)
        if token.count(":") == 1 and "/" not in token and not token.startswith("-")
    ]
    assert references, "no uvicorn app reference found in deploy/*.service"

    for reference in references:
        module_name, _, attr = reference.partition(":")
        module = importlib.import_module(module_name)
        assert hasattr(module, attr), f"{reference} names no attribute {attr!r}"
