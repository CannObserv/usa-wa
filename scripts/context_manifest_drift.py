"""Report tracked docs that the SocratiCode context manifest does not declare (#300).

#263 wired a daily health check for the *declared-but-unindexed* artifact. This
is the same class of failure from the other side, and the commoner one: a doc
that is simply absent from ``.socraticodecontextartifacts.json`` is invisible to
that check, because the check only inspects what the manifest already names. In
#298 that hid eleven ``docs/MODULES-*.md`` references plus ``ARCHITECTURE.md``,
``ONTOLOGY.md``, ``API.md`` and ``DEPLOYMENT.md`` — all indexable, none
reachable through ``codebase_context_search``, while the manifest still
described a "four-layer" repo the layering had outgrown at #189.

Scope is the agent-context surface: tracked Markdown at the repo root and
anywhere under ``docs/``. Package ``README.md`` files are module documentation,
not context artifacts, and are deliberately out.

Two entry points share this:

* ``.claude/hooks/context-manifest-drift.sh`` — the once-per-UTC-day reporter,
  measuring the primary checkout, silent when clean;
* ``scripts/tests/test_context_manifest_drift.py`` — the same check as a gate,
  which fails at the commit that introduces the drift.

Exit codes: 0 clean (or nothing to check), 1 findings, 2 the check could not run.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

MANIFEST = ".socraticodecontextartifacts.json"
EXEMPT_FILE = ".skills/context-artifacts-exempt"


def _tracked(project_root: Path) -> list[str]:
    """Every tracked path, in git's own order.

    Tracked, not globbed: scratch notes and worktree debris are not the repo's
    context surface, and an untracked file cannot be indexed from a fresh clone
    anyway. The whole list, not just the docs, so a stale exemption can be told
    apart from one naming a file that exists but is out of scope — "not tracked"
    would be false there, and would send the reader to delete a real file.
    """
    return subprocess.run(  # noqa: S603 — fixed argv, no shell, no user input
        ["git", "-c", "core.quotePath=false", "ls-files"],  # noqa: S607
        cwd=project_root,
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    ).stdout.splitlines()


def _in_scope(path: str) -> bool:
    """Markdown at the repo root or anywhere under ``docs/``.

    Package ``README.md`` files are module documentation, not context artifacts.
    """
    return path.endswith(".md") and (path.startswith("docs/") or "/" not in path)


def _declared(project_root: Path) -> set[str]:
    """Manifest paths, normalized off their ``./`` prefix."""
    data = json.loads((project_root / MANIFEST).read_text())
    return {
        str(Path(artifact["path"]))
        for artifact in data.get("artifacts", [])
        if artifact.get("path")
    }


def _read_list(path: Path) -> list[str]:
    """One path per line; blank lines and ``#`` comments ignored.

    The same grammar as ``.skills/doc-sensitive-paths`` and
    ``.skills/import-targets`` — one knob format for the whole ``.skills/`` dir.
    """
    if not path.is_file():
        return []
    return [
        stripped
        for line in path.read_text().splitlines()
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]


def _covered(doc: str, declared: set[str]) -> bool:
    """A doc is covered by its own declaration or by a declared directory above it."""
    parts = Path(doc).parts
    return any(str(Path(*parts[: i + 1])) in declared for i in range(len(parts)))


def findings(project_root: Path) -> list[str]:
    """Undeclared docs, then stale exemptions. Empty means clean."""
    if not (project_root / MANIFEST).is_file():
        return []  # never indexed here; there is no manifest to have drifted from

    declared = _declared(project_root)
    exempt = _read_list(project_root / EXEMPT_FILE)
    tracked = _tracked(project_root)
    docs = [path for path in tracked if _in_scope(path)]

    out = [
        f"undeclared context artifact: {doc}"
        for doc in docs
        if not _covered(doc, declared) and doc not in exempt
    ]
    # An opt-out nobody revisits is a blindfold. Each way it rots leaves an entry
    # that suppresses nothing while reading like a considered decision — and each
    # gets its own diagnosis, because "delete this line" and "the file is gone"
    # are different repairs.
    in_scope = set(docs)
    all_tracked = set(tracked)
    for entry in exempt:
        if entry in in_scope:
            if _covered(entry, declared):
                out.append(f"stale exemption ({EXEMPT_FILE}): {entry} is declared in {MANIFEST}")
        elif entry in all_tracked:
            out.append(
                f"stale exemption ({EXEMPT_FILE}): {entry} is outside the checked scope "
                "(Markdown at the repo root or under docs/), so exempting it does nothing"
            )
        else:
            out.append(f"stale exemption ({EXEMPT_FILE}): {entry} is not a tracked file")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--project-root",
        default=".",
        type=Path,
        help="checkout to measure (default: the current directory)",
    )
    args = parser.parse_args()

    try:
        found = findings(args.project_root.resolve())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        # Named, not swallowed. A checker that cannot run must not print the
        # clean-tree output — that is the #298 failure in miniature.
        print(f"context-manifest-drift: could not run: {exc}", file=sys.stderr)
        return 2

    if not found:
        return 0
    for line in found:
        print(f"  - {line}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
