"""Full-timeline committee rename-chain builder (sub-project 3, Phase B).

Pure function: given ``{biennium: {source_id: LongName}}`` across all archived
bienniums, walk each stable id's *consecutive appearances* and emit every
normalize_name transition as a windowed former→legal hop, with deep-history
guardrails (dormancy-aware, formatting-only ignored, per-boundary storm floor).
"""

from datetime import date

from usa_wa_sync_powermap import committee_name_chain as chain


def _by_sid(transitions):
    return {t.source_id: (t.former_name, t.legal_name, t.boundary_biennium) for t in transitions}


def test_single_rename_one_hop():
    cohorts = {
        "2021-22": {"1": "House Committee on Transportation"},
        "2023-24": {"1": "House Transportation Committee"},
    }
    result = chain.build_rename_chain(cohorts)
    assert _by_sid(result["transitions"]) == {
        "1": ("House Committee on Transportation", "House Transportation Committee", "2023-24")
    }


def test_multi_hop_chain_emits_each_transition():
    cohorts = {
        "2019-20": {"1": "Name A"},
        "2021-22": {"1": "Name B"},
        "2023-24": {"1": "Name B"},  # unchanged — no hop
        "2025-26": {"1": "Name C"},
    }
    result = chain.build_rename_chain(cohorts)
    hops = sorted((t.former_name, t.legal_name, t.boundary_biennium) for t in result["transitions"])
    assert hops == [
        ("Name A", "Name B", "2021-22"),
        ("Name B", "Name C", "2025-26"),
    ]


def test_formatting_only_change_is_not_a_rename():
    # normalize_name collapses case/punctuation/whitespace drift.
    cohorts = {
        "2021-22": {"1": "House Transportation Committee"},
        "2023-24": {"1": "House  Transportation   Committee"},
    }
    result = chain.build_rename_chain(cohorts)
    assert result["transitions"] == []


def test_dormancy_gap_compares_consecutive_appearances():
    # id absent from 2023-24 (dormant) then reappears renamed in 2025-26 → the name
    # persisted across the gap; the hop is 2021-22's name → 2025-26's name.
    cohorts = {
        "2021-22": {"1": "Old Name"},
        "2023-24": {},  # dormant
        "2025-26": {"1": "New Name"},
    }
    result = chain.build_rename_chain(cohorts)
    assert _by_sid(result["transitions"]) == {"1": ("Old Name", "New Name", "2025-26")}


def test_no_transition_for_first_appearance_only():
    cohorts = {"2025-26": {"1": "Only Ever Name"}}
    assert chain.build_rename_chain(cohorts)["transitions"] == []


def test_storm_boundary_is_skipped():
    # Many ids "rename" at one boundary → systematic reformat/re-key, not real renames.
    prior = {str(i): f"Name {i}" for i in range(10)}
    reformatted = {str(i): f"COMMITTEE: Name {i}" for i in range(10)}  # all 10 change
    cohorts = {"2023-24": prior, "2025-26": reformatted}
    result = chain.build_rename_chain(cohorts, max_rename_fraction=0.34, storm_floor=5)
    assert result["transitions"] == []
    assert result["storm_skipped"] == [{"biennium": "2025-26", "renamed": 10, "eligible": 10}]


def test_storm_floor_not_tripped_below_overlap():
    # 2 ids, both change: fraction 1.0 but overlap (2) is below the storm floor (5) →
    # not a storm, real renames kept.
    cohorts = {
        "2023-24": {"1": "A", "2": "C"},
        "2025-26": {"1": "B", "2": "D"},
    }
    result = chain.build_rename_chain(cohorts, max_rename_fraction=0.34, storm_floor=5)
    assert len(result["transitions"]) == 2
    assert result["storm_skipped"] == []


def test_effective_boundary_date_is_biennium_start():
    cohorts = {"2021-22": {"1": "Old"}, "2023-24": {"1": "New"}}
    t = chain.build_rename_chain(cohorts)["transitions"][0]
    # 2023-24 starts Jan 1 2023 — former window closes there, legal opens there.
    assert t.effective_start == date(2023, 1, 1)
    assert t.former_effective_end == date(2023, 1, 1)
