"""The parity harness core (#306): key-set diffs with explained acceptances."""

import pytest

from usa_wa_pipeline.parity import AcceptedDiff, key_set_parity


def test_clean_when_sets_match() -> None:
    report = key_set_parity("d", ["a", "b"], ["b", "a"])
    assert report.clean
    assert report.staging_total == 2


def test_divergence_reported_per_side() -> None:
    report = key_set_parity("d", ["a", "x"], ["a", "y"])
    assert not report.clean
    assert report.only_staging == {"x"}
    assert report.only_canonical == {"y"}
    assert "only in staging (1): x" in report.render()


def test_accepted_diff_removes_named_side() -> None:
    report = key_set_parity(
        "d",
        ["a", "x"],
        ["a"],
        accepted=[AcceptedDiff("x", "staging", "explained: source quirk")],
    )
    assert report.clean
    assert report.accepted[0].reason == "explained: source quirk"


def test_stale_acceptance_is_an_error() -> None:
    """An allowlist entry that no longer diverges is a blindfold — refuse it."""
    with pytest.raises(ValueError, match="stale parity acceptances"):
        key_set_parity("d", ["a"], ["a"], accepted=[AcceptedDiff("a", "staging", "old")])


def test_subset_parity_ignores_staging_surplus() -> None:
    from usa_wa_pipeline.parity import subset_parity

    report = subset_parity("d", ["a", "b", "extra"], ["a", "b"])
    assert report.clean
    assert report.staging_total == 3


def test_subset_parity_fails_on_canonical_loss() -> None:
    from usa_wa_pipeline.parity import subset_parity

    report = subset_parity("d", ["a"], ["a", "lost"])
    assert not report.clean
    assert report.only_canonical == {"lost"}
