"""The layering contracts exist, are wired into the gate, and actually fire (#189).

AR-14's finding was not that the layers were wrong but that they were a **claim**:
`docs/ARCHITECTURE.md` described a four-layer split while `usa-wa-adapter-sos` imported 21
symbols from two peer adapters and the PM sync sidecar imported a SOAP transport. The
restructure only stays done if it is checkable, so `import-linter` is the durable half of
this issue — and a fitness function nobody proved fires is itself a claim.

These tests pin three things:

1. every contract this issue wrote is still declared (a deleted contract is the silent way to
   "fix" a violation),
2. the contracts are wired into the same pre-commit gate ruff runs in, and
3. **the linter genuinely rejects a violation** — proved by injecting one temporary illegal
   import and watching the named contract break, rather than by trusting that a green run
   means anything.
"""

from __future__ import annotations

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

#: Every contract #189 wrote. Named individually so deleting one fails here, which is the
#: cheapest way a future change could make a violation "go away".
EXPECTED_CONTRACTS = {
    "Layers: core < domain < common < adapter < facts < deployment",
    "No adapter imports a peer adapter",
    "Deployment packages never touch an adapter transport",
    "Facts depend on cohort interfaces, never on a transport",
    "usa-wa-common is source-free",
}


def _config() -> dict:
    return tomllib.loads(PYPROJECT.read_text())


def _contracts() -> list[dict]:
    return _config()["tool"]["importlinter"]["contracts"]


def test_every_layering_contract_is_declared():
    assert {c["name"] for c in _contracts()} == EXPECTED_CONTRACTS


def test_every_workspace_package_is_a_root_package():
    """A package missing from `root_packages` is invisible to every contract — the same
    silent-omission failure mode `test_workspace_registries.py` exists for."""
    roots = set(_config()["tool"]["importlinter"]["root_packages"])
    packages = {
        p.name.replace("-", "_")
        for p in (REPO / "packages").iterdir()
        if (p / "pyproject.toml").is_file()
    }
    # The generated OpenAPI client is excluded from every hook (see .pre-commit-config.yaml);
    # it is vendored, not ours to layer.
    assert roots == packages - {"powermap_client"}


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
        # A Layer-4 deployment module driving a Layer-3 wire.
        (
            "packages/usa-wa-sync-powermap/src/usa_wa_sync_powermap/_contract_probe.py",
            "from usa_wa_adapter_legislature.transport import WSLClient  # noqa: F401",
            "Deployment packages never touch an adapter transport",
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
    assert f"{contract} BROKEN" in output, output
