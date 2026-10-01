"""Tests for scripts/rollover-rehearsal.sh — the #135 rollover rehearsal's wrapper.

The wrapper copies the production raw store and catalog into a scratch dir, points every
root at it, and runs the REAL nightly chain in its rehearsal mode under the next biennium.
These run it against a fake source checkout and a stub ``uv`` that records the environment
each stage saw, so nothing real is harvested, built or published.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "rollover-rehearsal.sh"

#: Shell-special characters on purpose: the env file is data, never evaluated.
DATABASE_URL = "postgresql://u:p$HOME`id`@prod/db?a=1&b=2"

STUB_UV = r"""#!/usr/bin/env bash
# Stub `uv`: names the stage, records the env a stage saw, succeeds.
stage=""
args=("$@")
for i in "${!args[@]}"; do
  if [ "${args[$i]}" = "-m" ]; then stage="${args[$((i + 1))]}"; break; fi
  if [ "${args[$i]}" = "dbt" ]; then stage="dbt"; break; fi
  case "${args[$i]}" in *rehearsal_roster.py) stage="rehearsal_roster"; break ;; esac
done
echo "stub-argv $stage $*"
echo "stub-log $stage"
for var in USA_WA_BIENNIUM USA_WA_RAW_ROOT USA_WA_PIPELINE_DB USA_WA_DATASETS_ROOT \
    USA_WA_JOB_LEDGER PIPELINE_NIGHTLY_REHEARSAL DATABASE_URL; do
  echo "stub-env $stage $var=${!var:-}"
done
case " ${STUB_FAIL:-} " in *" $stage "*) exit 1 ;; esac
"""


@pytest.fixture
def source(tmp_path_factory):
    """A fake production checkout: a raw store and a catalog, each with a marker file, and
    an env file that — like /etc/usa-wa/.env — points the raw root at production."""
    root = tmp_path_factory.mktemp("prod")
    (root / "raw" / "usa_wa_legislature").mkdir(parents=True)
    (root / "raw" / "usa_wa_legislature" / "latest.json").write_text("{}")
    (root / "data" / "datasets").mkdir(parents=True)
    (root / "data" / "datasets" / "catalog.json").write_text("{}")
    env_file = root / "env"
    env_file.write_text(f"USA_WA_RAW_ROOT={root}/raw\nDATABASE_URL={DATABASE_URL}\n")
    return root


#: Stub `systemctl`: `is-active` answers as STUB_PIPELINE says (default: not running).
STUB_SYSTEMCTL = r"""#!/usr/bin/env bash
[ "$1" = is-active ] || exit 1
[ "${STUB_PIPELINE:-inactive}" = active ]
"""


@pytest.fixture
def rehearse(tmp_path_factory, source):
    bin_dir = tmp_path_factory.mktemp("bin")
    stub = bin_dir / "uv"
    stub.write_text(STUB_UV)
    stub.chmod(0o755)
    systemctl = bin_dir / "systemctl"
    systemctl.write_text(STUB_SYSTEMCTL)
    systemctl.chmod(0o755)

    def _run(*args: str, **extra: str) -> tuple[int, list[str], Path]:
        scratch = tmp_path_factory.mktemp("scratch") / "run"
        proc = subprocess.run(
            ["bash", str(SCRIPT), *args],
            env={
                "PATH": "/usr/bin:/bin",
                "HOME": str(tmp_path_factory.getbasetemp()),
                "PIPELINE_NIGHTLY_UV": str(stub),
                "ROLLOVER_REHEARSAL_SOURCE": str(source),
                "ROLLOVER_REHEARSAL_ENV_FILES": str(source / "env"),
                "ROLLOVER_REHEARSAL_DIR": str(scratch),
                "ROLLOVER_REHEARSAL_SYSTEMCTL": str(systemctl),
                **extra,
            },
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=60,
            check=False,
        )
        return proc.returncode, proc.stdout.splitlines(), scratch

    return _run


def _env(lines: list[str], stage: str) -> dict[str, str]:
    prefix = f"stub-env {stage} "
    return dict(
        line.removeprefix(prefix).split("=", 1) for line in lines if line.startswith(prefix)
    )


def _stages(lines: list[str]) -> list[str]:
    return [line.removeprefix("stub-log ") for line in lines if line.startswith("stub-log ")]


def test_every_stage_sees_the_next_biennium_and_only_scratch_roots(rehearse) -> None:
    """The production env file is loaded (the build reads the registry through
    DATABASE_URL), then every root it names is overridden — its USA_WA_RAW_ROOT above all."""
    code, lines, scratch = rehearse("empty")

    assert code == 0, "\n".join(lines)
    for stage in ("usa_wa_adapter_legislature.raw_harvest", "dbt", "usa_wa_pipeline.publish"):
        env = _env(lines, stage)
        assert env["USA_WA_BIENNIUM"] == "2027-28"
        assert env["USA_WA_RAW_ROOT"] == f"{scratch}/raw"
        assert env["USA_WA_PIPELINE_DB"] == f"{scratch}/pipeline.duckdb"
        assert env["USA_WA_DATASETS_ROOT"] == f"{scratch}/datasets"
        assert env["USA_WA_JOB_LEDGER"] == "0"
        assert env["PIPELINE_NIGHTLY_REHEARSAL"] == str(scratch)


def test_the_scratch_dir_starts_as_a_copy_of_production(rehearse, source) -> None:
    """The shrink gate compares against today's catalog, so it is copied too."""
    _code, _lines, scratch = rehearse("empty")

    assert (scratch / "raw" / "usa_wa_legislature" / "latest.json").is_file()
    assert (scratch / "datasets" / "catalog.json").is_file()
    assert (scratch / ".rehearsal").is_file()
    assert (scratch / "nightly.log").is_file()


def test_production_is_left_untouched(rehearse, source) -> None:
    before = sorted(str(p.relative_to(source)) for p in source.rglob("*"))
    rehearse("empty")
    assert sorted(str(p.relative_to(source)) for p in source.rglob("*")) == before


def test_the_empty_scenario_harvests_live(rehearse) -> None:
    _code, lines, _scratch = rehearse("empty")

    assert "usa_wa_adapter_legislature.raw_harvest" in _stages(lines)
    assert "rehearsal_roster" not in _stages(lines)


def test_the_partial_scenario_builds_from_a_synthesized_roster(rehearse) -> None:
    """No harvest: a live one would re-fetch sponsors:2027-28 and supersede the wire."""
    code, lines, scratch = rehearse("partial")

    assert code == 0, "\n".join(lines)
    stages = _stages(lines)
    assert stages[0] == "rehearsal_roster"
    roster = next(line for line in lines if line.startswith("stub-argv rehearsal_roster "))
    assert f"--root {scratch}/raw --from 2025-26 --to 2027-28 --keep 0.6" in roster
    assert not [stage for stage in stages if stage.endswith(".raw_harvest")]
    assert "dbt" in stages


def test_an_unknown_scenario_is_refused_before_anything_is_copied(rehearse) -> None:
    code, _lines, scratch = rehearse("full")

    assert code == 2
    assert not scratch.exists()


def test_it_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)


def test_the_env_file_is_read_literally(rehearse) -> None:
    """Like `export $(cat … | xargs)`, never sourced: a `$`, a backtick or an `&` in a
    secret reaches the build as written."""
    _code, lines, _scratch = rehearse("empty")

    assert _env(lines, "dbt")["DATABASE_URL"] == DATABASE_URL


def test_the_chains_exit_code_is_the_rehearsals(rehearse) -> None:
    """A refused publish is a rehearsal finding, so the wrapper must not swallow it."""
    code, lines, _scratch = rehearse("empty", STUB_FAIL="usa_wa_pipeline.publish")

    assert code == 1
    assert any(line.startswith("rollover-rehearsal: empty exit 1") for line in lines)


def test_an_existing_scratch_dir_is_refused(tmp_path_factory, source) -> None:
    """CR 2: `cp -a` into an existing dir nests the copy (raw/raw) and the build reads a
    previous run's leftovers — a stale rehearsal reported as today's."""
    stale = tmp_path_factory.mktemp("stale")
    (stale / "pipeline.duckdb").write_text("yesterday")
    stub = tmp_path_factory.mktemp("bin") / "uv"
    stub.write_text(STUB_UV)
    stub.chmod(0o755)
    proc = subprocess.run(
        ["bash", str(SCRIPT), "empty"],
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path_factory.getbasetemp()),
            # the stub even here: a regression must never reach the real chain
            "PIPELINE_NIGHTLY_UV": str(stub),
            "ROLLOVER_REHEARSAL_SOURCE": str(source),
            "ROLLOVER_REHEARSAL_ENV_FILES": str(source / "env"),
            "ROLLOVER_REHEARSAL_DIR": str(stale),
        },
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=60,
        check=False,
    )

    assert proc.returncode == 2
    assert "exists" in proc.stdout
    assert sorted(p.name for p in stale.iterdir()) == ["pipeline.duckdb"]


def test_a_rehearsal_waits_for_the_nightly_to_finish(rehearse) -> None:
    """CR 9: copying the raw store and catalog mid-nightly can catch a half-written run —
    objects without their manifest, a catalog mid-publish — and rehearse a state production
    never had. Refused, before anything is copied."""
    code, lines, scratch = rehearse("empty", STUB_PIPELINE="active")

    assert code == 2
    assert any("usa-wa-pipeline.service" in line for line in lines)
    assert not scratch.exists()


def test_an_idle_nightly_lets_the_rehearsal_run(rehearse) -> None:
    code, _lines, scratch = rehearse("empty", STUB_PIPELINE="inactive")

    assert code == 0
    assert (scratch / "raw").is_dir()
