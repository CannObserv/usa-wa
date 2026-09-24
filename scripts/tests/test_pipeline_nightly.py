"""Tests for scripts/pipeline-nightly.sh — the nightly #302 chain's closing report (#331).

The #49 alert email carries only the unit's last 25 journal lines, and the chain
prints about 1,300: on the 2026-09-24 run the three harvesters' ``job_finished``
summaries sat at lines 3, 12 and 27. A failed stage's own summary line — the
counters ``JobFailure`` exists to keep — never reached the email (#331 CR 5). The
script therefore restates each failed stage's last stdout line at the very end.

Every test runs the real script against a stub ``uv`` in a tmp root, through the
two test seams. The seams are checked before anything runs: without them the
script ``cd``s to the primary checkout and runs ``/usr/local/bin/uv`` — the real
harvests, build and publish.
"""

from __future__ import annotations

import os
import socket
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parent.parent / "pipeline-nightly.sh"  # scripts/tests/ → scripts/

ALERT_TAIL = 25
"""Lines of journal ``scripts/notify-failure.sh`` puts in the email."""

STUB_UV = r"""#!/usr/bin/env bash
# Stub `uv`: names the stage from its argv, logs, then succeeds or fails on cue.
stage=""
args=("$@")
for i in "${!args[@]}"; do
  if [ "${args[$i]}" = "-m" ]; then stage="${args[$((i + 1))]}"; break; fi
  if [ "${args[$i]}" = "dbt" ]; then stage="dbt"; break; fi
done
case " ${STUB_SILENT:-} " in *" $stage "*) exit 1 ;; esac
echo "stub-log $stage"
for _ in $(seq 1 "${STUB_NOISE:-0}"); do echo "stub-noise $stage"; done
case " ${STUB_FAIL:-} " in
  *" $stage "*) echo "job=$stage outcome=failed fetched=3"; exit "${STUB_RC:-1}" ;;
esac
case " ${STUB_COLOR:-} " in
  *" $stage "*) printf '\033[0m08:06:34  \033[31mDone. PASS=110 ERROR=1\033[0m\n'; exit 1 ;;
esac
case " ${STUB_NO_NEWLINE:-} " in
  *" $stage "*) printf 'job=%s outcome=failed fetched=5' "$stage"; exit 1 ;;
esac
echo "job=$stage outcome=ok"
"""


def _refuse_without_seams() -> None:
    """Never run the real chain: the seams must exist before the script is executed."""
    text = SCRIPT.read_text()
    for seam in ('"${PIPELINE_NIGHTLY_ROOT:-', '"${PIPELINE_NIGHTLY_UV:-'):
        if seam not in text:
            pytest.fail(f"{SCRIPT.name} does not honour {seam}…}} — refusing to run it")


@pytest.fixture
def run_nightly(tmp_path):
    """Run the script with a stub ``uv``; return (exit code, journal-ordered lines).

    stderr is merged into stdout, the order the journal records them in, so the
    returned tail is what the alert email would carry.
    """
    _refuse_without_seams()
    stub = tmp_path / "uv"
    stub.write_text(STUB_UV)
    stub.chmod(0o755)

    def _run(*, journal_socket: bool = False, **stub_env: str) -> tuple[int, list[str]]:
        env = {
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "PIPELINE_NIGHTLY_ROOT": str(tmp_path),
            "PIPELINE_NIGHTLY_UV": str(stub),
            **stub_env,
        }
        if not journal_socket:
            proc = subprocess.run(
                ["bash", str(SCRIPT)],
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=60,
                check=False,
            )
            return proc.returncode, proc.stdout.splitlines()
        # Under systemd both streams are a socket to journald, not a pipe — and a
        # socket, unlike a pipe, cannot be reopened through /dev/stdout (ENXIO).
        reader, writer = socket.socketpair()
        with reader, writer:
            proc = subprocess.run(
                ["bash", str(SCRIPT)],
                env=env,
                stdout=writer.fileno(),
                stderr=writer.fileno(),
                timeout=60,
                check=False,
            )
            writer.shutdown(socket.SHUT_WR)
            chunks = iter(lambda: reader.recv(65536), b"")
            return proc.returncode, b"".join(chunks).decode().splitlines()

    return _run


def test_a_clean_night_exits_zero_and_restates_nothing(run_nightly):
    code, lines = run_nightly()

    assert code == 0
    assert not [line for line in lines if "failed stage" in line]


def test_every_stage_still_streams_its_output(run_nightly):
    """The capture is a copy: each stage's output still reaches the journal."""
    _, lines = run_nightly(STUB_FAIL="usa_wa_pipeline.publish")

    assert "stub-log usa_wa_adapter_legislature.raw_harvest" in lines
    assert "job=usa_wa_adapter_sos.raw_harvest outcome=ok" in lines
    assert "stub-log usa_wa_pipeline.parity_citations" in lines


def test_a_failed_harvest_reaches_the_alert_tail(run_nightly):
    """The #331 case: a harvest fails first, hundreds of lines precede the end,
    and its summary — counters included — is still in the email."""
    code, lines = run_nightly(STUB_FAIL="usa_wa_adapter_legislature.raw_harvest", STUB_NOISE="200")

    assert code == 1
    tail = "\n".join(lines[-ALERT_TAIL:])
    assert (
        "usa_wa_adapter_legislature.raw_harvest (exit 1): "
        "job=usa_wa_adapter_legislature.raw_harvest outcome=failed fetched=3"
    ) in tail
    assert "pipeline-nightly: 1 stage(s) failed" in tail


def test_each_failed_stage_is_restated_with_its_own_exit_code(run_nightly):
    code, lines = run_nightly(
        STUB_FAIL="usa_wa_adapter_pdc.raw_harvest usa_wa_pipeline.registrar", STUB_RC="4"
    )

    assert code == 1
    restated = [line for line in lines if "failed stage" in line]
    assert len(restated) == 2
    assert "usa_wa_adapter_pdc.raw_harvest (exit 4)" in restated[0]
    assert "usa_wa_pipeline.registrar (exit 4)" in restated[1]


def test_a_build_abort_still_restates_the_failures_before_it(run_nightly):
    """A failed build aborts the chain — the harvest failures that preceded it
    (and likely caused it) must not be dropped by the early exit."""
    code, lines = run_nightly(STUB_FAIL="usa_wa_adapter_sos.raw_harvest dbt", STUB_NOISE="200")

    assert code == 1
    assert "stub-log usa_wa_pipeline.registrar" not in lines
    tail = "\n".join(lines[-ALERT_TAIL:])
    assert "usa_wa_adapter_sos.raw_harvest (exit 1): job=usa_wa_adapter_sos" in tail
    assert "dbt build (exit 1): job=dbt outcome=failed" in tail


def test_a_stage_with_no_stdout_is_still_named(run_nightly):
    """A crash before the harness prints its summary (an import error) leaves
    nothing on stdout; the stage is named anyway."""
    code, lines = run_nightly(STUB_SILENT="usa_wa_pipeline.publish")

    assert code == 1
    assert any("usa_wa_pipeline.publish (exit 1): (no summary on stdout)" in line for line in lines)


def test_the_closing_report_survives_stdout_on_the_journal_socket(run_nightly):
    """The capture must never open ``/dev/stdout`` or ``/dev/fd/N``: on the journal
    socket that is ENXIO, and a pipe — what the other tests use — would hide it."""
    code, lines = run_nightly(journal_socket=True, STUB_FAIL="usa_wa_adapter_pdc.raw_harvest")

    assert code == 1
    assert "job=usa_wa_adapter_pdc.raw_harvest outcome=failed fetched=3" in lines
    assert not [line for line in lines if "No such device or address" in line]
    assert lines[-1].startswith("pipeline-nightly: failed stage: usa_wa_adapter_pdc.raw_harvest")


def test_the_capture_needs_no_disk(run_nightly, tmp_path):
    """CR 8: on a disk-full night — the CR 1 case, and ``/tmp`` shares ``/`` with the
    raw store — a file-backed capture either claims "no summary" or restates a stale
    line. The capture is in memory, so an unusable ``TMPDIR`` changes nothing."""
    code, lines = run_nightly(
        STUB_FAIL="usa_wa_adapter_pdc.raw_harvest", TMPDIR=str(tmp_path / "does-not-exist")
    )

    assert code == 1
    assert (
        "pipeline-nightly: failed stage: usa_wa_adapter_pdc.raw_harvest (exit 1): "
        "job=usa_wa_adapter_pdc.raw_harvest outcome=failed fetched=3"
    ) in lines


def test_a_last_line_without_a_newline_is_still_captured(run_nightly):
    code, lines = run_nightly(STUB_NO_NEWLINE="usa_wa_pipeline.publish")

    assert code == 1
    assert (
        "pipeline-nightly: failed stage: usa_wa_pipeline.publish (exit 1): "
        "job=usa_wa_pipeline.publish outcome=failed fetched=5"
    ) in lines


def test_a_colored_summary_is_restated_as_plain_text(run_nightly):
    """CR 9: dbt colors its output off a TTY too — its real last line is
    ``ESC[0m08:06:34  Done. PASS=…`` — and journalctl hands the escapes to the
    email raw. The restatement strips them; the stage's own lines are untouched."""
    code, lines = run_nightly(STUB_COLOR="dbt")

    assert code == 1
    assert lines[-1] == (
        "pipeline-nightly: failed stage: dbt build (exit 1): 08:06:34  Done. PASS=110 ERROR=1"
    )
    assert "\x1b[31mDone." in "\n".join(lines[:-1])


def test_the_seams_default_to_the_production_paths():
    """Unset, the script runs exactly what it ran before the seams existed."""
    text = SCRIPT.read_text()
    assert '"${PIPELINE_NIGHTLY_ROOT:-/home/exedev/usa-wa}"' in text
    assert '"${PIPELINE_NIGHTLY_UV:-/usr/local/bin/uv run --frozen --no-sync}"' in text
    assert os.access(SCRIPT, os.X_OK)
