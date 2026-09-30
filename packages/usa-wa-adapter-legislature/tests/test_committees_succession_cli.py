"""Committee-succession CLI (usa-wa#124 C2) — validation + record + supersede + batch."""

import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import select

from clearinghouse_core.job import load_json_batch
from clearinghouse_core.rawstore import RAW_ROOT_ENV, RawStore
from clearinghouse_core.registry import KIND_ORG, RegistryEntity, RegistryKey
from clearinghouse_core.testing import patch_job_runtime
from clearinghouse_domain_legislative.identity import Organization
from clearinghouse_domain_legislative.operator_events import OPERATOR_SOURCE_SLUG
from usa_wa_adapter_legislature.committees import succession_cli as cli
from usa_wa_adapter_legislature.committees.succession_cli import (
    LinkSpec,
    SuccessionError,
    load_specs,
    validate_and_record,
)
from usa_wa_adapter_legislature.committees.succession_store import get_or_create_operator_source
from usa_wa_adapter_legislature.operators.raw import PendingAttestations
from usa_wa_common.jurisdiction import resolve_jurisdiction
from usa_wa_common.orgs import STRUCTURAL_ORGS


async def _source(session):
    return await get_or_create_operator_source(session, await resolve_jurisdiction(session))


async def _register_org(session, source_id):
    """Bind ``usa_wa_legislature:<source_id>`` to a fresh registry org entity."""
    entity = RegistryEntity(kind=KIND_ORG)
    session.add(entity)
    await session.flush()
    session.add(
        RegistryKey(
            kind=KIND_ORG,
            natural_key=f"usa_wa_legislature:{source_id}",
            entity_id=entity.id,
            registered_by="test",
        )
    )
    await session.flush()


async def _committee(session, source_id):
    """A committee as the registry knows it — the identity authority since #412 froze
    canonical (#445). No canonical row: a committee born after the freeze has none."""
    await _register_org(session, source_id)


def _link(
    subject="14294",
    linked="28244",
    slug="succeeded_by",
    year=2021,
    supersede_id=None,
    clear_year=False,
):
    return LinkSpec(
        subject_source_id=subject,
        linked_source_id=linked,
        slug=slug,
        evidence_url="https://example.gov/x",
        effective_year=year,
        supersede_id=supersede_id,
        clear_year=clear_year,
    )


async def test_records_a_valid_link(db_session, usa_wa):
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    source = await _source(db_session)
    event = await validate_and_record(db_session, source, _link())
    assert event.slug == "succeeded_by"
    assert event.subject_source_id == "14294"
    assert event.linked_source_id == "28244"


async def test_unknown_slug_rejected(db_session, usa_wa):
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="unknown slug"):
        await validate_and_record(db_session, source, _link(slug="dissolved"))


async def test_identical_ends_rejected(db_session, usa_wa):
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="must differ"):
        await validate_and_record(db_session, source, _link(subject="14294", linked="14294"))


async def test_unresolvable_subject_rejected(db_session, usa_wa):
    await _committee(db_session, "28244")  # linked exists; subject does not
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="--subject"):
        await validate_and_record(db_session, source, _link(subject="00000"))


async def test_unresolvable_linked_rejected(db_session, usa_wa):
    await _committee(db_session, "14294")  # subject exists; linked does not
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="--linked"):
        await validate_and_record(db_session, source, _link(linked="00000"))


@pytest.mark.parametrize("structural_id", sorted(STRUCTURAL_ORGS))
async def test_structural_org_rejected(db_session, usa_wa, structural_id):
    """A registered structural org (legislature, chamber, party) is not a link end."""
    await _committee(db_session, "14294")
    await _register_org(db_session, structural_id)
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="--linked"):
        await validate_and_record(db_session, source, _link(linked=structural_id))


async def test_other_body_with_a_negative_wsl_id_links(db_session, usa_wa):
    """WSL numbers some Other bodies negatively (JLARC is ``-5``) — still a committee id."""
    await _committee(db_session, "-5")
    await _committee(db_session, "21488")
    source = await _source(db_session)
    event = await validate_and_record(  # the I-900 subcommittee split from JLARC
        db_session, source, _link(subject="21488", linked="-5", slug="split_from", year=None)
    )
    assert event.linked_source_id == "-5"


async def test_registered_non_integer_key_rejected(db_session, usa_wa):
    """CR 2: a registered key that is no WSL committee Id — e.g. a structural org since
    dropped from ``STRUCTURAL_ORGS``, whose key the registry keeps forever — is refused."""
    await _committee(db_session, "14294")
    await _register_org(db_session, "party-whig")
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="--linked"):
        await validate_and_record(db_session, source, _link(linked="party-whig"))


async def test_joint_committee_born_after_the_canonical_freeze_links(db_session, usa_wa):
    """#445: the Civic Health re-key — a Joint body (canonical ``org_type='other'``)
    registered only by the registrar, after #412 froze canonical — is a valid end."""
    await _committee(db_session, "35341")
    await _committee(db_session, "36500")
    source = await _source(db_session)
    event = await validate_and_record(
        db_session, source, _link(subject="35341", linked="36500", year=2026)
    )
    assert (event.subject_source_id, event.linked_source_id) == ("35341", "36500")


async def test_a_key_on_a_merged_entity_still_links(db_session, usa_wa):
    """CR 3: a tombstoned entity's key still names a live body (its merge survivor), so
    it links — registered means live; the resolver must not demand ``merged_into IS NULL``."""
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    await _committee(db_session, "31000")
    key = await db_session.scalar(
        select(RegistryKey).where(RegistryKey.natural_key == "usa_wa_legislature:28244")
    )
    survivor = await db_session.scalar(
        select(RegistryKey.entity_id).where(RegistryKey.natural_key == "usa_wa_legislature:31000")
    )
    (await db_session.get(RegistryEntity, key.entity_id)).merged_into = survivor
    await db_session.flush()
    source = await _source(db_session)
    event = await validate_and_record(db_session, source, _link())
    assert event.linked_source_id == "28244"


async def test_canonical_only_committee_rejected(db_session, usa_wa):
    """A canonical committee row the registry never bound is not an identity (#445)."""
    await _committee(db_session, "14294")
    db_session.add(
        Organization(source="usa_wa_legislature", source_id="28244", name="C", org_type="committee")
    )
    await db_session.flush()
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="--linked"):
        await validate_and_record(db_session, source, _link())


async def test_supersede_relink(db_session, usa_wa):
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    await _committee(db_session, "99999")
    source = await _source(db_session)
    prior = await validate_and_record(db_session, source, _link(linked="99999"))
    corrected = await validate_and_record(
        db_session, source, _link(linked="28244", supersede_id=str(prior.id))
    )
    assert corrected.id != prior.id
    assert prior.superseded_by_id == corrected.id


async def test_supersede_clear_year(db_session, usa_wa):
    """``--clear-year`` on a supersede removes the boundary year (vs omitting it = inherit)."""
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    source = await _source(db_session)
    prior = await validate_and_record(db_session, source, _link(year=2021))
    corrected = await validate_and_record(
        db_session, source, _link(year=None, clear_year=True, supersede_id=str(prior.id))
    )
    assert corrected.id != prior.id
    assert corrected.effective_year is None
    assert prior.superseded_by_id == corrected.id


async def test_supersede_inherits_year_when_omitted(db_session, usa_wa):
    """Omitting the year on a supersede inherits the prior link's year (not clear)."""
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    await _committee(db_session, "99999")
    source = await _source(db_session)
    prior = await validate_and_record(db_session, source, _link(linked="99999", year=2021))
    corrected = await validate_and_record(
        db_session, source, _link(linked="28244", year=None, supersede_id=str(prior.id))
    )
    assert corrected.effective_year == 2021  # inherited, not cleared


async def test_clear_year_requires_supersede(db_session, usa_wa):
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    source = await _source(db_session)
    with pytest.raises(SuccessionError, match="clear-year"):
        await validate_and_record(db_session, source, _link(clear_year=True))


async def test_supersede_slug_mismatch_rejected(db_session, usa_wa):
    await _committee(db_session, "14294")
    await _committee(db_session, "28244")
    source = await _source(db_session)
    prior = await validate_and_record(db_session, source, _link(slug="succeeded_by"))
    with pytest.raises(SuccessionError, match="slug"):
        await validate_and_record(
            db_session,
            source,
            _link(slug="split_from", supersede_id=str(prior.id)),
        )


def test_load_specs_parses_batch():
    specs = load_specs(
        [
            {
                "subject": "14294",
                "linked": "28244",
                "slug": "succeeded_by",
                "year": 2021,
                "evidence_url": "https://x",
            },
            {
                "subject": "20900",
                "linked": "31639",
                "slug": "split_from",
                "evidence_url": "https://y",
            },
        ]
    )
    assert len(specs) == 2
    assert specs[0].effective_year == 2021
    assert specs[1].effective_year is None
    assert specs[1].slug == "split_from"


def test_load_specs_missing_field_rejected():
    with pytest.raises(SuccessionError, match="missing required field"):
        load_specs([{"subject": "1", "linked": "2", "slug": "succeeded_by"}])


async def test_the_file_batch_is_read_off_the_event_loop(tmp_path, monkeypatch):
    """``--file`` is read in a worker thread, not on the loop (#196).

    The read used to sit inline in the handler coroutine behind a ``# noqa: ASYNC230``.
    Practical impact is nil — one file, one job, no concurrency to starve — but the
    suppression made the gate lie, so the shape is pinned here rather than asserted in
    a comment.
    """
    path = tmp_path / "links.json"
    path.write_text(
        json.dumps(
            [
                {
                    "subject": "14294",
                    "linked": "28244",
                    "slug": "succeeded_by",
                    "year": 2021,
                    "evidence_url": "https://x",
                }
            ]
        )
    )
    read_threads: list[int] = []
    real_read_text = Path.read_text

    def _recording(self, *args, **kwargs):
        read_threads.append(threading.get_ident())
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", _recording)

    specs = await load_json_batch(str(path), cli.load_specs)

    assert [s.subject_source_id for s in specs] == ["14294"]
    assert read_threads, "the batch file was never read"
    assert threading.get_ident() not in read_threads


async def test_a_malformed_file_batch_still_raises_its_succession_error(tmp_path):
    """Moving the read off the loop must not move where its errors surface (#196)."""
    path = tmp_path / "links.json"
    path.write_text(json.dumps({"subject": "1"}))
    with pytest.raises(SuccessionError, match="JSON array"):
        await load_json_batch(str(path), cli.load_specs)


# --- CLI (#179b: the shared job harness) --------------------------------------


def test_main_validation_failure_is_still_exit_two(monkeypatch, capsys):
    """Documented contract (COMMANDS-SUCCESSION.md): exit 2 on a validation failure.
    Preserved via ``JobResult.failed(..., exit_code=EXIT_CONFIG)`` — the ledger records
    the honest ``failed`` outcome while the operator-facing code stays 2."""
    recording = patch_job_runtime(monkeypatch)

    async def _reject(_session, _args, _raw):
        raise SuccessionError("a single link needs --subject --linked --slug --evidence-url")

    with patch.object(cli, "_run", _reject):
        code = cli.main(["--subject", "924"])  # missing --linked/--slug/--evidence-url

    assert code == 2
    assert (recording.committed, recording.rolled_back) == (0, 1)
    assert "a single link needs" in capsys.readouterr().err


def test_main_dry_run_rolls_back(monkeypatch):
    """--dry-run validates + writes, then rolls back — the harness owns the rollback."""
    recording = patch_job_runtime(monkeypatch)

    async def _fake_run(_session, _args, _raw):
        return 0

    with patch.object(cli, "_run", _fake_run):
        assert cli.main(["--dry-run", "--list"]) == 0

    # --list is read-only, so the harness commits an empty transaction rather than
    # rolling back the listing (matching the pre-#179b `dry_run and not list` branch).
    assert (recording.committed, recording.rolled_back) == (1, 0)


# --- raw store (#412 PR A) ----------------------------------------------------


def _attesting_run(error=None):
    """A ``_run`` stand-in that buffers one attestation, then optionally fails."""

    async def _run(_session, _args, raw):
        raw.add("succeeded_by:14294:28244", b"{}", datetime(2026, 9, 25, tzinfo=UTC))
        if error is not None:
            raise error
        return 0

    return _run


def _manifests(root):
    return RawStore(root, OPERATOR_SOURCE_SLUG).manifest_paths()


def test_main_flushes_attestations_to_the_raw_store_after_commit(monkeypatch, tmp_path):
    """The raw manifest lands only once the transaction it describes has committed."""
    monkeypatch.setenv(RAW_ROOT_ENV, str(tmp_path))
    recording = patch_job_runtime(monkeypatch)
    commits_at_flush: list[int] = []
    real_flush = PendingAttestations.flush

    def _flush(self):
        commits_at_flush.append(recording.committed)
        return real_flush(self)

    monkeypatch.setattr(PendingAttestations, "flush", _flush)

    with patch.object(cli, "_run", _attesting_run()):
        assert cli.main(["--subject", "14294"]) == 0

    assert commits_at_flush == [1]
    assert len(_manifests(tmp_path)) == 1


def test_main_dry_run_writes_nothing_to_the_raw_store(monkeypatch, tmp_path):
    monkeypatch.setenv(RAW_ROOT_ENV, str(tmp_path))
    recording = patch_job_runtime(monkeypatch)

    with patch.object(cli, "_run", _attesting_run()):
        assert cli.main(["--dry-run", *["--subject", "14294"]]) == 0

    assert recording.rolled_back == 1
    assert not (tmp_path / OPERATOR_SOURCE_SLUG).exists()


def test_main_validation_failure_writes_nothing_to_the_raw_store(monkeypatch, tmp_path):
    monkeypatch.setenv(RAW_ROOT_ENV, str(tmp_path))
    patch_job_runtime(monkeypatch)

    with patch.object(cli, "_run", _attesting_run(SuccessionError("rejected"))):
        assert cli.main(["--subject", "14294"]) == 2

    assert not (tmp_path / OPERATOR_SOURCE_SLUG).exists()


def test_main_a_failed_archive_is_degraded_not_failed(monkeypatch, tmp_path, capsys):
    """The commit landed; only the raw copy did not. Exit 4, with the re-run advice."""
    monkeypatch.setenv(RAW_ROOT_ENV, str(tmp_path))
    recording = patch_job_runtime(monkeypatch)

    def _broken(self):
        raise PermissionError("raw/ is read-only")

    monkeypatch.setattr(PendingAttestations, "flush", _broken)
    with patch.object(cli, "_run", _attesting_run()):
        assert cli.main(["--subject", "14294"]) == 4

    assert recording.committed == 1
    assert "uv run python -m clearinghouse_core.raw_export" in capsys.readouterr().err
