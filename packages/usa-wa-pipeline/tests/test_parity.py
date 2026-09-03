"""The parity harness core (#306): key-set diffs with explained acceptances."""

import pytest

from usa_wa_pipeline.parity import AcceptedDiff, key_set_parity, subset_parity


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
        accepted=[AcceptedDiff("d", "x", "staging", "explained: source quirk")],
    )
    assert report.clean
    assert report.accepted[0].reason == "explained: source quirk"


def test_stale_acceptance_is_an_error() -> None:
    """An allowlist entry that no longer diverges is a blindfold — refuse it."""
    with pytest.raises(ValueError, match="stale parity acceptances"):
        key_set_parity("d", ["a"], ["a"], accepted=[AcceptedDiff("d", "a", "staging", "old")])


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


def test_acceptance_vanished_from_both_sides_is_stale() -> None:
    """A key gone from BOTH sides means the divergence healed — the acceptance
    must die loudly, not be skipped (#302 CR: no presence pre-filtering)."""
    gone = AcceptedDiff("d", "ghost", "canonical", "healed divergence")
    with pytest.raises(ValueError, match="stale"):
        key_set_parity("d", ["a"], ["a"], accepted=[gone])


def test_acceptance_is_scoped_to_its_dataset() -> None:
    """The same key in another dataset's keyspace is neither swallowed nor
    reported stale there — acceptances act only on their own dataset."""
    other = AcceptedDiff("sponsors", "31656", "canonical", "Heck ex-officio")
    report = key_set_parity("committees", ["31656", "a"], ["a"], accepted=[other])
    assert report.only_staging == frozenset({"31656"})  # not swallowed
    # and in its own dataset it must match or fail
    report = key_set_parity("sponsors", ["a"], ["a", "31656"], accepted=[other])
    assert report.clean
    assert report.accepted == (other,)


def test_subset_parity_rejects_staging_side_acceptance() -> None:
    """A staging-side acceptance is meaningless in subset mode: it would match
    against a surplus the report discards by construction."""
    bad = AcceptedDiff("d", "x", "staging", "meaningless here")
    with pytest.raises(ValueError, match="subset"):
        subset_parity("d", ["x", "a"], ["a"], accepted=[bad])
