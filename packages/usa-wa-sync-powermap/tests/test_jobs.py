"""The PM producer CLIs' shared exit contract (#179b).

Twelve CLIs published the same 0/1/2/3 mapping inline. These pin it once, including the
split that matters for #178: the operator-facing exit code is the family's convention,
while the ledger ``outcome`` stays comparable with every other job in the repo.
"""

import json

import pytest

from clearinghouse_core.runs import OUTCOME_DEGRADED, OUTCOME_FAILED, OUTCOME_OK
from clearinghouse_sync_powermap.client import DeliveryBlockedError
from usa_wa_sync_powermap.jobs import (
    EXIT_ABORTED,
    EXIT_AUTH_BLOCKED,
    never,
    pm_job_result,
    rows_unsettled,
    run_pm_job,
)


def test_clean_run_is_ok_and_zero():
    result = pm_job_result({"observed": 3, "rejected": 0})
    assert (result.outcome, result.resolved_exit_code()) == (OUTCOME_OK, 0)


def test_rejected_rows_are_failed_and_one():
    summary = {"observed": 3, "rejected": 1}
    result = pm_job_result(summary, failed=rows_unsettled(summary))
    assert (result.outcome, result.resolved_exit_code()) == (OUTCOME_FAILED, 1)


def test_a_guardrail_abort_is_degraded_on_three():
    """The defining 'ran to completion, took no action' case. ``3`` is the code operators
    and units already read here — the harness leaves it free for exactly this."""
    result = pm_job_result({"aborted": "rename_storm", "rejected": 0})
    assert (result.outcome, result.resolved_exit_code()) == (OUTCOME_DEGRADED, EXIT_ABORTED)


def test_an_abort_wins_over_rejected_rows():
    """An aborted run took no action, so its rejected tally cannot be the headline."""
    summary = {"aborted": "empty_cohort", "rejected": 2}
    result = pm_job_result(summary, failed=rows_unsettled(summary))
    assert result.resolved_exit_code() == EXIT_ABORTED


def test_never_opts_a_job_out_of_the_rejected_rule():
    summary = {"rejected": 5}
    assert never(summary) is False
    assert pm_job_result(summary, failed=never(summary)).resolved_exit_code() == 0


async def test_auth_block_is_failed_on_two_with_a_stderr_diagnostic(capsys):
    async def _blocked():
        raise DeliveryBlockedError("PM 403")

    result = await run_pm_job(_blocked)

    assert (result.outcome, result.resolved_exit_code()) == (OUTCOME_FAILED, EXIT_AUTH_BLOCKED)
    captured = capsys.readouterr()
    assert "delivery blocked" in json.loads(captured.err)["error"]
    # stdout belongs to the harness's run summary; a second JSON object there would break
    # anything parsing it.
    assert captured.out == ""


async def test_run_pm_job_grades_a_normal_summary():
    async def _work():
        return {"observed": 2, "rejected": 1}

    result = await run_pm_job(_work)
    assert result.resolved_exit_code() == 1


async def test_run_pm_job_honours_a_custom_failed_rule():
    async def _work():
        return {"rejected": 1}

    result = await run_pm_job(_work, failed_when=never)
    assert result.resolved_exit_code() == 0


def test_the_codes_are_the_documented_ones():
    """COMMANDS-SYNC.md: 0 clean; 1 rejected; 2 auth block; 3 guardrail abort."""
    assert (EXIT_AUTH_BLOCKED, EXIT_ABORTED) == (2, 3)


@pytest.mark.parametrize(
    ("summary", "expected"),
    [({}, False), ({"rejected": 0, "failed": 0}, False), ({"failed": 2}, True)],
)
def test_rows_unsettled_reads_both_tallies(summary, expected):
    assert rows_unsettled(summary) is expected
