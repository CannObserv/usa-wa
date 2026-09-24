"""Pin docs/COMMANDS.md's command index against the job-harness entry points (#237 CR 12).

The index opens "Every operational & backfill CLI, grouped by the reference that
documents it", and nothing checked the claim. ``roster_pdf.build`` (#228) shipped with
no row while the count beside the table ("All 51") read as plausibly as it was wrong;
the omission surfaced only when #237's curation round moved the roster rows. The #167
ratchet, applied to commands: the doc is pinned against the thing it describes, in both
directions.

The fleet is ``_job_scan``'s — the one definition the harness-adoption and dry-run
guards already share — so "is a CLI" means the same thing in all three.

Pure file parse — no job is imported, no DB.
"""

import re
from pathlib import Path

from _job_scan import REPO, jobs, relative

INDEX = REPO / "docs" / "COMMANDS.md"

#: A table row that names a command: ``| `python -m pkg.mod` | purpose |``. Rows only —
#: prose may quote a command without being an index entry.
ROW_RE = re.compile(r"^\| `python -m ([\w.]+)", re.MULTILINE)


def indexed_modules(text: str) -> set[str]:
    """Every module a table row of ``text`` names as ``python -m <module>``."""
    return set(ROW_RE.findall(text))


def module_name(path: Path) -> str:
    """The dotted name ``python -m`` takes for an entry-point file."""
    dotted = relative(path).removesuffix(".py").replace("/", ".")
    return dotted.removesuffix(".__main__")


def job_modules() -> set[str]:
    """Every job-harness entry point, as ``python -m`` names it."""
    return {module_name(path) for path in jobs()}


def test_the_scan_found_the_fleet() -> None:
    """A guard over an empty scan passes for the wrong reason."""
    assert len(job_modules()) >= 40


def test_every_job_has_an_index_row() -> None:
    missing = job_modules() - indexed_modules(INDEX.read_text())
    assert not missing, (
        f"docs/COMMANDS.md has no index row for {sorted(missing)} — add one under the "
        "reference that documents it"
    )


def test_every_index_row_names_a_job() -> None:
    stale = indexed_modules(INDEX.read_text()) - job_modules()
    assert not stale, (
        f"docs/COMMANDS.md indexes {sorted(stale)}, which no longer run_job — the module "
        "was renamed or removed; update or delete the row"
    )


def test_the_index_names_this_guard() -> None:
    """The doc points at its guard, so a rename cannot leave the pointer dangling."""
    assert Path(__file__).name in INDEX.read_text()


def test_only_table_rows_count_as_entries() -> None:
    """Has teeth on the parse: prose quoting a command is not an index row."""
    text = (
        "| `python -m pkg.indexed` | a row |\n"
        "Run `python -m pkg.quoted` by hand after a backfill.\n"
        "    python -m pkg.in_a_code_block\n"
    )
    assert indexed_modules(text) == {"pkg.indexed"}


def test_module_name_handles_a_package_main() -> None:
    """``python -m pkg`` runs ``pkg/__main__.py``; the index writes the package name."""
    path = REPO / "packages" / "x" / "src" / "pkg" / "__main__.py"
    assert module_name(path) == "pkg"
