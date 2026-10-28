"""The layering contracts exist, are wired into the gate, and actually fire (#189).

AR-14's finding was not that the layers were wrong but that they were a **claim**:
`docs/ARCHITECTURE.md` described a four-layer split while `usa-wa-adapter-sos` imported 21
symbols from two peer adapters and the PM sync sidecar imported a SOAP transport. The
restructure only stays done if it is checkable, so `import-linter` is the durable half of
this issue — and a fitness function nobody proved fires is itself a claim.

These tests pin five things:

1. every contract this issue wrote is still declared (a deleted contract is the silent way to
   "fix" a violation),
2. the contracts are wired into the same pre-commit gate ruff runs in,
3. **the linter genuinely rejects a violation** — proved by injecting one temporary illegal
   import and watching the named contract break, rather than by trusting that a green run
   means anything,
4. **the layer order says what it means** (#533): the pipeline is its own layer directly above
   facts, and every ``:`` sibling group is backed by an independence contract, because ``:``
   alone permits imports between the siblings it groups, and
5. **every wire is banned** (#551): both transport bans list exactly the adapters'
   ``transport`` modules, checked at commit by the ``transport-bans`` hook, so a new wire
   cannot ship off the list the way ``roster_pdf.transport`` did from #225 to #551.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).parent.parent.parent  # scripts/tests/ → repo
#: The console script beside the running interpreter — `python -m importlinter.cli`
#: exits 0 with no output (there is no `__main__` guard), which would make every
#: assertion below vacuous.
LINT_IMPORTS = Path(sys.executable).parent / "lint-imports"
PYPROJECT = REPO / "pyproject.toml"
PRECOMMIT = REPO / ".pre-commit-config.yaml"

TIER_CONTRACT = "The pipeline, the API and the raw harvests never import the retired Postgres tier"
LAYERS_CONTRACT = "Layers: core < domain < common < adapter < facts < pipeline < deployment"
DEPLOYMENT_TRANSPORT_BAN = "Deployment packages never touch an adapter transport"
FACTS_TRANSPORT_BAN = "Facts and the pipeline never depend on a transport"

#: Every contract #189 wrote. Named individually so deleting one fails here, which is the
#: cheapest way a future change could make a violation "go away".
EXPECTED_CONTRACTS = {
    LAYERS_CONTRACT,
    "No adapter imports a peer adapter",
    DEPLOYMENT_TRANSPORT_BAN,
    FACTS_TRANSPORT_BAN,
    "usa-wa-common is source-free",
    # #412 PR D: what survives the Postgres tier must not reach its write path.
    TIER_CONTRACT,
}


def _config() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def _contracts() -> list[dict]:
    return _config()["tool"]["importlinter"]["contracts"]


def test_every_layering_contract_is_declared():
    assert {c["name"] for c in _contracts()} == EXPECTED_CONTRACTS


def _layers() -> list[str]:
    return next(c for c in _contracts() if c["name"] == LAYERS_CONTRACT)["layers"]


def test_every_sibling_group_is_backed_by_an_independence_contract():
    """A ``:`` in a layers entry is not a claim that its members are independent (#533).

    import-linter separates *non-independent* siblings with ``:`` and independent ones with
    ``|``, so ``usa_wa_facts_seats : usa_wa_pipeline`` let both import each other from #303
    until #533 while its comment said "neither may import the other". The adapter line's ``:``
    is fine only because "No adapter imports a peer adapter" says it separately. Pin that
    pairing, so a sibling group's independence is always a contract and never a comment.
    """
    independent = [frozenset(c["modules"]) for c in _contracts() if c["type"] == "independence"]
    for entry in _layers():
        siblings = frozenset(m.strip() for m in entry.split(":"))
        if len(siblings) > 1:
            assert siblings in independent, (
                f"layers entry {entry!r} groups siblings with ':' (which allows imports "
                "between them) and no independence contract covers exactly that group"
            )


def test_the_pipeline_is_the_layer_above_facts():
    """#533 (option A): the pipeline composes facts' pure logic (#309 ``conformed.house``,
    #490 ``conformed.movers``), so it is a layer of its own directly above facts — which
    makes the edge that matters, facts → pipeline, a back-edge the contract refuses."""
    layers = _layers()
    assert "usa_wa_pipeline" in layers and "usa_wa_facts_seats" in layers, layers
    assert layers.index("usa_wa_pipeline") == layers.index("usa_wa_facts_seats") - 1, layers


def test_the_facts_transport_contract_carries_no_exceptions():
    """#201 closed the two `ignore_imports` #189 deferred.

    `house/refresh.py` and `pdc/refresh.py` each ran a source's Phase-A harvest (live client)
    *and* rebuilt the fact from the resulting archive; only the second half is a fact. The
    archive half moved to the adapters (`…results.archive_refresh`, `…pdc.archive_refresh`,
    both deleted in #412 PR F), so the contract now holds unaided. An exception is the cheapest
    way to re-weld a fact to a single source — the failure the 2026-07 votewa outage taught —
    and the previous pair carried a *false* provenance claim ("tracked as the follow-on named
    in MODULES-FACTS-SEATS.md", which said no such thing) for two releases. Adding one back
    must be a deliberate act that fails here, not a line in a table nobody re-reads.
    """
    contract = next(c for c in _contracts() if c["name"] == FACTS_TRANSPORT_BAN)
    assert not contract.get("ignore_imports"), (
        "the facts→transport contract has exceptions again: "
        f"{contract.get('ignore_imports')}. Move the sourcing half into the adapter instead."
    )


def test_both_transport_bans_name_the_same_wires():
    """The deployment's ban and the facts/pipeline ban list their transports separately, and
    agree on purpose (#535): a wire no deployment may drive is one no fact or model may drive
    either. Adding a transport to one list and not the other fails here rather than leaving
    a layer silently able to drive it."""
    by_name = {c["name"]: c for c in _contracts()}
    deployment = by_name[DEPLOYMENT_TRANSPORT_BAN]
    facts = by_name[FACTS_TRANSPORT_BAN]
    assert sorted(deployment["forbidden_modules"]) == sorted(facts["forbidden_modules"])


def transport_modules(packages: Path) -> set[str]:
    """Every module named ``transport`` under ``packages/usa-wa-adapter-*/src``: a
    ``transport.py`` or a ``transport/`` package, at any depth.

    Adapters only: the bans forbid *adapter* wires, and a deployment's own outbound call
    (the API's backup check-in, #455) is no source's wire. Held here, it would make the
    deployment ban forbid the API its own module."""
    found = set()
    for src in packages.glob("usa-wa-adapter-*/src"):
        for path in src.rglob("*.py"):
            parts = path.relative_to(src).with_suffix("").parts
            if parts[-1] == "__init__":
                parts = parts[:-1]
            if parts and parts[-1] == "transport":
                found.add(".".join(parts))
    return found


def test_the_transport_scan_goes_by_module_name(tmp_path):
    """A module or package named ``transport`` counts at any depth under an adapter's
    ``src``; a name that merely contains the word, a file outside ``src``, or a
    non-adapter package's ``transport`` (an API's own outbound call, say) does not."""
    for relative in (
        "usa-wa-adapter-a/src/pkg_a/transport.py",
        "usa-wa-adapter-a/src/pkg_a/roster/transport/__init__.py",
        "usa-wa-adapter-a/src/pkg_a/transports.py",
        "usa-wa-adapter-a/src/pkg_a/test_transport.py",
        "usa-wa-adapter-a/tests/transport.py",
        "usa-wa-api/src/usa_wa_api/backup/transport.py",
    ):
        (tmp_path / relative).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / relative).write_text("")
    assert transport_modules(tmp_path) == {"pkg_a.transport", "pkg_a.roster.transport"}


def test_every_adapter_transport_is_banned():
    """Both bans list exactly the adapters' ``transport`` modules (#551).

    ``roster_pdf.transport`` (#225, an httpx wire to leg.wa.gov) sat on neither list until
    #551, so any fact, model or API module could have driven it and ``lint-imports`` passed.

    By name, not by what a module imports. ``ARCHITECTURE.md``'s Layer-3 pattern puts a
    source's wire in its ``transport`` module, and the bans name modules, so the name is the
    unit both already work in. An import rule (``httpx``/``zeep``/``requests``/``urllib``)
    measured nine modules on #551's base: the five transports, three legislature modules
    that import only ``zeep.exceptions.Fault``, and the API's own backup check-in (#455).
    Separating those needs an exemption list, which #527 refused for the same kind of
    scan, and the library list is a convention too: a wire built on another client would
    slip it just as a wire named something else slips this. The accepted limit is that
    one: a wire outside a ``transport`` module is missed here.

    Equality, not a subset: an entry with no module behind it keeps its ban vacuously, so
    a renamed or deleted transport fails here until the lists follow it.
    """
    wires = transport_modules(REPO / "packages")
    assert len(wires) > 1, "found no adapter transports - the scan broke"
    by_name = {c["name"]: c for c in _contracts()}
    for ban in (DEPLOYMENT_TRANSPORT_BAN, FACTS_TRANSPORT_BAN):
        listed = set(by_name[ban]["forbidden_modules"])
        assert listed == wires, (
            f"{ban!r}: unlisted wires {sorted(wires - listed)}, "
            f"entries with no transport behind them {sorted(listed - wires)}"
        )


def test_the_tier_contract_follows_indirect_imports():
    """#412 PR F deleted the write path, so a chain through any module counts as much as a
    direct import: the pipeline reached ``span_emit`` only through ``roster_pdf.build``.
    ``allow_indirect_imports`` would let exactly that chain back in, and so would an
    exception, which is the cheapest way to make a violation "go away"."""
    contract = next(c for c in _contracts() if c["name"] == TIER_CONTRACT)
    assert not contract.get("allow_indirect_imports", False)
    assert not contract.get("ignore_imports")


#: The canonical tier's model modules, deleted in #412 PR F. PR D could not forbid them:
#: the parity probes and ``registry_seed`` imported them until PR F removed both.
CANONICAL_MODELS = {
    "clearinghouse_core.provenance",
    "clearinghouse_domain_legislative.identity",
    "clearinghouse_domain_legislative.bills",
    "clearinghouse_domain_legislative.sessions",
    "clearinghouse_domain_legislative.votes",
    "clearinghouse_domain_legislative.statutes",
    "clearinghouse_domain_legislative.pdc",
}


def test_the_tier_contract_tombstones_the_canonical_models():
    """A missing forbidden module is vacuously kept, so the entries cost nothing now and
    refuse the import the day any of those names comes back."""
    contract = next(c for c in _contracts() if c["name"] == TIER_CONTRACT)
    assert CANONICAL_MODELS <= set(contract["forbidden_modules"])


def test_every_workspace_package_is_a_root_package():
    """A package missing from `root_packages` is invisible to every contract — the same
    silent-omission failure mode `test_workspace_registries.py` exists for."""
    roots = set(_config()["tool"]["importlinter"]["root_packages"])
    packages = {
        p.name.replace("-", "_")
        for p in (REPO / "packages").iterdir()
        if (p / "pyproject.toml").is_file()
    }
    # No exclusions since #314 deleted `powermap-client`, the generated OpenAPI
    # client that was vendored rather than ours to layer. Every package on disk
    # is now a package the contracts see.
    assert roots == packages


def test_import_linter_runs_in_the_same_gate_as_ruff():
    """A contract that only runs when someone remembers is not enforcement."""
    config = yaml.safe_load(PRECOMMIT.read_text())
    hooks = [h for repo in config["repos"] for h in repo["hooks"]]
    by_id = {h["id"]: h for h in hooks}
    assert "import-linter" in by_id, "import-linter is not wired into pre-commit"
    hook = by_id["import-linter"]
    assert "lint-imports" in hook["entry"]
    # Whole-graph analysis: a file list would make the result depend on what is staged.
    assert hook["pass_filenames"] is False
    assert hook["always_run"] is True


def test_contracts_are_currently_kept():
    """The tree satisfies its own layering."""
    result = subprocess.run(  # noqa: S603
        [str(LINT_IMPORTS), "--no-cache"],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize(
    ("module", "illegal_import", "contract"),
    [
        # An adapter reaching for a peer adapter — the exact shape AR-14 found 22 files of.
        (
            "packages/usa-wa-adapter-sos/src/usa_wa_adapter_sos/_contract_probe.py",
            "from usa_wa_adapter_pdc import adapter  # noqa: F401",
            "No adapter imports a peer adapter",
        ),
        # A Layer-4 deployment module driving a Layer-3 wire. The probe moved here
        # in #314: the sidecar it used to sit in was the contract's original
        # offender AND its original probe, and deleting the package would have
        # quietly taken the proof with it. The API is the deployment layer now, so
        # the rule is proved against the package it still governs.
        (
            "packages/usa-wa-api/src/usa_wa_api/_contract_probe.py",
            "from usa_wa_adapter_legislature.transport import WSLClient  # noqa: F401",
            DEPLOYMENT_TRANSPORT_BAN,
        ),
        # A fact driving a wire — the shape #189 had to grant two exceptions for and #201
        # removed. Unlike the others this contract was never proved to fire, because the only
        # two violations in the tree were exempted.
        (
            "packages/usa-wa-facts-seats/src/usa_wa_facts_seats/_contract_probe.py",
            "from usa_wa_adapter_pdc.transport import PDCClient  # noqa: F401",
            FACTS_TRANSPORT_BAN,
        ),
        # The pipeline driving the roster-PDF wire (#551): the one transport neither ban
        # listed from #225 until #551. The pipeline is the likeliest offender: it already
        # imports that subpackage's pure modules (`roster_pdf.identity`, `roster_pdf.audit`).
        (
            "packages/usa-wa-pipeline/src/usa_wa_pipeline/_contract_probe_roster_wire.py",
            "from usa_wa_adapter_legislature.roster_pdf import transport  # noqa: F401",
            FACTS_TRANSPORT_BAN,
        ),
        # A fact reaching up into the pipeline that composes it (#533). Until then the
        # layers entry grouped the two with ':', which permits exactly this, and no probe
        # had ever proved the layers contract fires at all.
        (
            "packages/usa-wa-facts-seats/src/usa_wa_facts_seats/_contract_probe_layers.py",
            "from usa_wa_pipeline.conformed import house  # noqa: F401",
            LAYERS_CONTRACT,
        ),
        # The pipeline reaching a Postgres writer directly (#412 PR D) — the shape
        # conformed.spans had with roster_pdf.build. PR F deleted that builder; the operator
        # store is the writer the list still names.
        (
            "packages/usa-wa-pipeline/src/usa_wa_pipeline/_contract_probe.py",
            "from usa_wa_adapter_legislature.operators import store  # noqa: F401",
            TIER_CONTRACT,
        ),
        # ... and through a module that is not itself forbidden: the operator CLI is not on
        # the list, but it imports the store, so the chain counts.
        (
            "packages/usa-wa-pipeline/src/usa_wa_pipeline/_contract_probe_indirect.py",
            "from usa_wa_adapter_legislature.operators import cli  # noqa: F401",
            TIER_CONTRACT,
        ),
        # Vocabulary reaching down into a source — how the first shared kernel formed.
        (
            "packages/usa-wa-common/src/usa_wa_common/_contract_probe.py",
            "from usa_wa_adapter_legislature import transport  # noqa: F401",
            "usa-wa-common is source-free",
        ),
    ],
)
def test_a_real_violation_is_rejected(module, illegal_import, contract):
    """Introduce one illegal import, assert the linter catches it, then remove it.

    Written into the real tree on purpose: `import-linter` resolves modules through the
    installed (editable) workspace, so a copied tree would be analysed against the original
    packages and the probe would be invisible — a green run that proves nothing, which is
    the exact failure this test exists to rule out. The probe is a single file under a
    `try/finally`, and `test_contracts_are_currently_kept` above re-establishes the clean
    state.

    Without this, "contracts pass" is indistinguishable from "contracts match nothing".
    """
    probe = REPO / module
    assert not probe.exists(), f"{module} already exists — pick another probe name"
    probe.write_text(f'"""Temporary layering-contract probe."""\n\n{illegal_import}\n')
    try:
        result = subprocess.run(  # noqa: S603
            [str(LINT_IMPORTS), "--no-cache"],
            cwd=REPO,
            capture_output=True,
            text=True,
        )
    finally:
        probe.unlink()
    output = result.stdout + result.stderr
    assert result.returncode != 0, f"the linter accepted an illegal import:\n{output}"
    # The report wraps at the terminal width, so a long contract name can break across
    # lines — and a verdict wrapped onto the next line loses its leading space (#535:
    # "…a transportBROKEN" at COLUMNS=50). Compare with all whitespace removed.
    assert "".join(f"{contract} BROKEN".split()) in "".join(output.split()), output


#: The scan's two tests, as the ``transport-bans`` hook names them (#551).
TRANSPORT_SCAN_TESTS = (
    test_the_transport_scan_goes_by_module_name,
    test_every_adapter_transport_is_banned,
)


def test_the_transport_bans_are_checked_at_commit():
    """The scan runs at commit, on every file that can change its verdict (#551).

    ``lint-imports`` checks the bans as written and never sees an unlisted wire, so before
    this hook a new ``transport`` committed without pre-ship landed unbanned and waited for
    the next suite run. Named by node id, not ``-k``: a renamed test fails the hook loudly
    instead of matching nothing.
    """
    config = yaml.safe_load(PRECOMMIT.read_text())
    by_id = {h["id"]: h for repo in config["repos"] for h in repo["hooks"]}
    assert "transport-bans" in by_id, "transport-bans is not wired into pre-commit"
    hook = by_id["transport-bans"]
    this = Path(__file__).relative_to(REPO).as_posix()
    for test in TRANSPORT_SCAN_TESTS:
        assert f"{this}::{test.__name__}" in hook["entry"].split(), hook["entry"]
    assert "--no-cov" in hook["entry"], "neither coverage gate measures a slice"
    # Filenames appended to a pytest command are collected as test files.
    assert hook["pass_filenames"] is False
    files = re.compile(hook["files"])
    wires = sorted(
        path.relative_to(REPO).as_posix()
        for path in REPO.glob("packages/usa-wa-adapter-*/src/**/transport.py")
    )
    triggers = [
        *wires,
        "packages/usa-wa-adapter-sos/src/usa_wa_adapter_sos/votes/transport/__init__.py",
        "packages/usa-wa-adapter-sos/src/transport.py",  # a top-level wire the scan counts
        "pyproject.toml",  # the bans
        this,  # the scan
        "scripts/tests/conftest.py",  # makes the suite's helpers importable
    ]
    assert len(wires) > 1, "found no adapter transports - the glob broke"
    assert [t for t in triggers if not files.search(t)] == []
    for other in (
        "packages/usa-wa-api/src/usa_wa_api/backup/transport.py",
        "packages/usa-wa-adapter-sos/src/usa_wa_adapter_sos/transports.py",
        "packages/usa-wa-adapter-sos/tests/transport.py",
        "packages/usa-wa-pipeline/pyproject.toml",
    ):
        assert not files.search(other), other
