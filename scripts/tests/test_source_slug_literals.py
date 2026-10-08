"""Every source slug has one home, and every copy that cannot import it is pinned (#245, #482).

``usa_wa_legislature`` is the join key between a writer and its readers: the raw-store
directory a harvest writes and a staging model reads, the namespace of every registry
natural key (``usa_wa_legislature:<member_id>``), and the published ``source`` column. A
rename that reaches only one side matches **nothing**, and fails silently — registry
lookups miss, spans drop as unregistered, and a gate fires far from the cause. When #245
was filed the literal had been retyped in 31 ``src`` files.

So each slug is declared once, in its adapter's ``coverage`` module, and every reader imports
it: :data:`usa_wa_adapter_legislature.coverage.WSL_SOURCE_SLUG` and its roster-PDF sibling
:data:`…roster_pdf.coverage.ROSTER_SOURCE_SLUG` (#245);
:data:`usa_wa_adapter_pdc.coverage.PDC_SOURCE_SLUG` and the two SOS feeds'
:data:`usa_wa_adapter_sos.coverage.SOS_FILINGS_SOURCE_SLUG` /
:data:`~usa_wa_adapter_sos.coverage.SOS_RESULTS_SOURCE_SLUG` (#482). The PDC person's registry
namespace, ``wa_pdc``, is the same bug class under a different spelling — the left-hand key
of the PDC↔WSL rule and the namespace two conformed builders filter on — so it is held to the
same rule: :data:`usa_wa_adapter_pdc.coverage.PDC_KEY_NAMESPACE`.

This guard keeps it that way: a quoted slug literal anywhere in tracked package source is a
failure unless it sits at one of the :data:`ALLOWED` sites below, each of which says why it
cannot import the constant. The SQL and YAML copies are then pinned **to the constant**, so
renaming it fails here until they follow.

Tests are out of scope: a test that spells the value out literally is a pin, not a copy.
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from pathlib import Path

import yaml

from usa_wa_adapter_legislature.coverage import WSL_SOURCE_SLUG
from usa_wa_adapter_legislature.roster_pdf.coverage import ROSTER_SOURCE_SLUG
from usa_wa_adapter_pdc.coverage import PDC_KEY_NAMESPACE, PDC_SOURCE_SLUG
from usa_wa_adapter_sos.coverage import SOS_FILINGS_SOURCE_SLUG, SOS_RESULTS_SOURCE_SLUG

REPO = Path(__file__).parent.parent.parent  # scripts/tests/ → repo

#: A quoted slug, as a whole string, a ``slug:`` key prefix or a ``slug/`` raw-store path.
#: Docstring mentions (````usa_wa_legislature````, ````raw/usa_wa_legislature/````) are prose
#: and do not match — which is why a leading ``/`` is not accepted.
SLUG_LITERAL = re.compile(
    r"""["'](usa_wa_legislature(?:_roster)?|usa_wa_pdc|usa_wa_sos(?:_results)?|wa_pdc)(?=["':/])"""
)

_SCANNED_SUFFIXES = {".py", ".sql", ".yml", ".yaml"}

_MATCH_PDC_WSL = "packages/usa-wa-pipeline/dbt/models/matching/match_pdc_wsl.sql"
_CONFORMED_SCHEMA = "packages/usa-wa-pipeline/dbt/models/conformed/schema.yml"

#: path → the slug literals it may hold, and how many times.
ALLOWED: dict[str, Counter[str]] = {
    # The declarations — each adapter's coverage module (slugs, plus PDC's key namespace).
    "packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/coverage.py": Counter(
        {WSL_SOURCE_SLUG: 1}
    ),
    "packages/usa-wa-adapter-legislature/src/usa_wa_adapter_legislature/roster_pdf/coverage.py": (
        Counter({ROSTER_SOURCE_SLUG: 1})
    ),
    "packages/usa-wa-adapter-pdc/src/usa_wa_adapter_pdc/coverage.py": Counter(
        {PDC_SOURCE_SLUG: 1, PDC_KEY_NAMESPACE: 1}
    ),
    "packages/usa-wa-adapter-sos/src/usa_wa_adapter_sos/coverage.py": Counter(
        {SOS_FILINGS_SOURCE_SLUG: 1, SOS_RESULTS_SOURCE_SLUG: 1}
    ),
    # A different fact that happens to share the spelling: the Legislature *organization's*
    # source id (its registry key is `usa_wa_legislature:usa_wa_legislature`). Renaming the
    # source must not re-key that entity, and Layer 2b may not import an adapter anyway.
    "packages/usa-wa-common/src/usa_wa_common/orgs.py": Counter({"usa_wa_legislature": 1}),
    # dbt SQL and YAML cannot import Python; pinned to the constants below.
    _MATCH_PDC_WSL: Counter({PDC_KEY_NAMESPACE: 1, WSL_SOURCE_SLUG: 1}),
    _CONFORMED_SCHEMA: Counter({WSL_SOURCE_SLUG: 1, ROSTER_SOURCE_SLUG: 1}),
}


def _tracked_package_sources() -> list[str]:
    """Tracked, non-test files under ``packages/`` that could carry a slug copy."""
    listed = subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
        ["git", "-c", "core.quotePath=false", "ls-files", "packages"],  # noqa: S607
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.splitlines()
    return [
        path
        for path in listed
        if Path(path).suffix in _SCANNED_SUFFIXES and Path(path).parts[2] != "tests"
    ]


def _slug_literals(path: str) -> Counter[str]:
    return Counter(SLUG_LITERAL.findall((REPO / path).read_text()))


def test_the_pattern_matches_every_copy_shape():
    """The scan is only as good as its regex; prove it sees each shape a copy took."""
    shapes = [
        'SOURCE = "usa_wa_legislature"',
        "RawStore(root, 'usa_wa_legislature_roster')",
        '"usa_wa_legislature:" + member_id',
        'f"usa_wa_legislature:{member_id}"',
        "'usa_wa_legislature:' || s.member_id",
        'root / "usa_wa_legislature/2025-26"',
        'RawStore(get_raw_root(), "usa_wa_pdc")',
        'RawStore(root, "usa_wa_sos")',
        "Source.slug == 'usa_wa_sos_results'",
        "'wa_pdc:' || p.person_id",
        'f"wa_pdc:{person_id}"',
        'key["key_namespace"] == "wa_pdc"',
    ]
    assert [SLUG_LITERAL.findall(shape) for shape in shapes] == [
        ["usa_wa_legislature"],
        ["usa_wa_legislature_roster"],
        ["usa_wa_legislature"],
        ["usa_wa_legislature"],
        ["usa_wa_legislature"],
        ["usa_wa_legislature"],
        ["usa_wa_pdc"],
        ["usa_wa_sos"],
        ["usa_wa_sos_results"],
        ["wa_pdc"],
        ["wa_pdc"],
        ["wa_pdc"],
    ]
    assert SLUG_LITERAL.findall("the ``usa_wa_legislature`` source") == []
    # A different identifier that merely contains a slug is not a copy of it.
    assert SLUG_LITERAL.findall('"person_wa_pdc"') == []


def test_no_package_source_retypes_a_source_slug():
    """Every slug literal outside :data:`ALLOWED` is a copy that should import the constant."""
    found = {path: _slug_literals(path) for path in _tracked_package_sources()}
    unexpected = {
        path: dict(literals)
        for path, literals in found.items()
        if literals and literals != ALLOWED.get(path)
    }
    assert unexpected == {}, (
        "import the slug's constant from its adapter's coverage module instead of retyping it "
        f"(or justify a new site in ALLOWED): {unexpected}"
    )


def test_every_allowed_site_still_exists():
    """A stale entry would let a new copy hide at a path nobody reads."""
    missing = [path for path in ALLOWED if not (REPO / path).is_file()]
    assert missing == [], f"ALLOWED names files that are gone: {missing}"
    assert {path: _slug_literals(path) for path in ALLOWED} == ALLOWED


def test_match_pdc_wsl_keys_the_wsl_namespace():
    """The PDC↔WSL rule's right-hand key must land in the registrar's person namespace."""
    sql = (REPO / _MATCH_PDC_WSL).read_text()
    assert f"'{WSL_SOURCE_SLUG}:' || s.member_id as right_key" in sql


def test_match_pdc_wsl_keys_the_pdc_namespace():
    """Its left-hand key must land where the conformed builders look for a PDC person."""
    sql = (REPO / _MATCH_PDC_WSL).read_text()
    assert f"'{PDC_KEY_NAMESPACE}:' || p.person_id as left_key" in sql


def test_conformed_assignments_source_accepts_exactly_the_two_slugs():
    """The published ``source`` column's accepted values are the two declared slugs."""
    models = yaml.safe_load((REPO / _CONFORMED_SCHEMA).read_text())["models"]
    accepted = [
        test["accepted_values"]["arguments"]["values"]
        for model in models
        for column in model.get("columns", [])
        if column["name"] == "source"
        for test in column.get("data_tests", [])
        if isinstance(test, dict) and "accepted_values" in test
    ]
    assert accepted == [[WSL_SOURCE_SLUG, ROSTER_SOURCE_SLUG]]
