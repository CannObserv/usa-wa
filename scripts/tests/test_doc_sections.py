"""The shipping gate's advice half, tailored to this repo (#371).

``doc-check.sh`` (Step 1.5 of the shipping skill) resolves two files
independently: ``.skills/doc-sensitive-paths`` says WHAT the gate watches, and
``.skills/doc-sections`` says WHAT TO DO about a hit. This repo tailored the
first in #297 and left the second on the vendored python-fastapi defaults, so
every hit printed two lines about "project structure, conventions, skill
inventory, route table" and never named the doc that had actually drifted.
Upstream gregoryfoster/skills#284 made that state audible — a hit now ends with
a ``Note: this project tailors .skills/doc-sensitive-paths but not
.skills/doc-sections`` — and filing it here is what #371 is.

Upstream deliberately runs **no** dead-entry check on the advice, because
advice is prose and a checker for it would be satisfied by pasting paths into
the text. The two checks below are the ones this repo has already been burned
by and can decide locally:

* every doc an advice line names is a tracked file — #314 deleted
  ``descriptors/`` along with ``docs/MODULES-SYNC-PRODUCERS.md`` and
  ``docs/LWW-NOOP-GATE.md``, and the routing #371 was filed with still named
  all three;
* every watched path is routed by some advice line — otherwise the gate flags
  a file and the advice printed beneath it is silent about why.

Neither is decidable upstream, and both are cheap here, where the two lists sit
side by side in ``.skills/``.
"""

import fnmatch
import re

from doc_check_lists import DOC_SECTIONS_FILE, SENSITIVE_PATHS_FILE, VENDORED, entries, tracked

#: The vendored python-fastapi advice. Committing either verbatim would tailor
#: the file's existence and nothing else — the #371 state with the note
#: silenced, which is strictly worse than not having the file.
DEFAULT_SECTIONS = frozenset(
    {
        "AGENTS.md: project structure, conventions, skill inventory, route table",
        (
            "README.md: orientation + curated links into canonical docs; only the "
            "README-owned bits (e.g. top-level CLI list, two-line quick start) "
            "should change here"
        ),
    }
)

#: A line's trailing parenthesised group: the sensitive-path entries it routes.
#: Anchored at end-of-line and parenthesis-free inside, so an ``(e.g. …)`` aside
#: earlier in the prose is not mistaken for the routing list.
ROUTES_RE = re.compile(r"\(([^()]*)\)\s*$")


def _docs_named(entry: str) -> list[str]:
    """The doc paths an advice line opens with, before its first ``:``."""
    head = entry.split(":", 1)[0]
    return [d for part in head.split(",") if (d := part.strip())]


def _routes(entry: str) -> list[str]:
    """The sensitive-path entries an advice line's trailing group names."""
    match = ROUTES_RE.search(entry)
    if not match:
        return []
    return [p for part in match.group(1).split(",") if (p := part.strip())]


def test_the_list_exists_and_parses() -> None:
    assert DOC_SECTIONS_FILE.is_file(), (
        "no .skills/doc-sections; with .skills/doc-sensitive-paths tailored, every "
        "gate hit prints the vendored python-fastapi advice plus a note saying so (#371)"
    )
    assert entries(DOC_SECTIONS_FILE), (
        "an empty list is exit 2 upstream, not a pass — remove the file instead"
    )


def test_no_vendored_default_survived_the_tailoring() -> None:
    """Keeping a default line routes this repo's hits at the skill's layout."""
    kept = DEFAULT_SECTIONS & set(entries(DOC_SECTIONS_FILE))
    assert not kept, f"vendored default advice still present: {sorted(kept)}"


def test_every_line_names_a_doc_and_routes_at_least_one_path() -> None:
    """The grammar the two checks below depend on, asserted once."""
    malformed = [
        entry
        for entry in entries(DOC_SECTIONS_FILE)
        if ":" not in entry or not _docs_named(entry) or not _routes(entry)
    ]
    assert not malformed, (
        "each line is `<doc>[, <doc>…]: <what to spot-check> (<path>, …)`; "
        f"these do not parse: {malformed}"
    )


def test_every_doc_named_is_a_tracked_file() -> None:
    """Advice pointing at a deleted doc sends the reader nowhere.

    Upstream declines this check because advice is prose; here the docs are
    named in a fixed position, so it costs nothing. #314 is the precedent —
    it deleted two of the docs the routing filed with #371 named.
    """
    tracked_files = set(tracked())
    dead = [
        doc
        for entry in entries(DOC_SECTIONS_FILE)
        for doc in _docs_named(entry)
        if doc not in tracked_files and not fnmatch.filter(tracked_files, doc)
    ]
    assert not dead, f"advice names docs that are not tracked files: {dead}"


def test_every_watched_path_is_routed() -> None:
    """A hit whose advice says nothing about it is the #371 complaint, narrowed.

    ``.skills/doc-sensitive-paths`` is the gate's input; this file is what the
    reader gets back. An entry added to one and not the other reproduces the
    silence — the gate flags a file and the advice beneath it is about other
    files. #314 dropped ``descriptors/`` from the path list; nothing would have
    noticed advice still routing it.
    """
    routed = {path for entry in entries(DOC_SECTIONS_FILE) for path in _routes(entry)}
    unrouted = [entry for entry in entries(SENSITIVE_PATHS_FILE) if entry not in routed]
    assert not unrouted, (
        f"watched paths no advice line names: {unrouted} — add them to a line's "
        "trailing group in .skills/doc-sections"
    )


def test_no_advice_routes_an_unwatched_path() -> None:
    """The other direction: advice about a path the gate cannot hit is dead prose."""
    watched = set(entries(SENSITIVE_PATHS_FILE))
    stray = sorted(
        {
            path
            for entry in entries(DOC_SECTIONS_FILE)
            for path in _routes(entry)
            if path not in watched
        }
    )
    assert not stray, f"advice routes paths absent from .skills/doc-sensitive-paths: {stray}"


def test_the_vendored_gate_still_reads_this_file() -> None:
    """Pin the premise: the advice override is what doc-check.sh consults.

    A vendored script that stopped reading it would leave this file asserting a
    tailoring the gate no longer applies, and the failure mode is the silent one
    gregoryfoster/skills#284 was filed for.
    """
    assert VENDORED.is_file(), f"vendored gate missing at {VENDORED}"
    source = VENDORED.read_text()
    assert ".skills/doc-sections" in source, (
        "the vendored doc-check.sh does not read the project's advice list; refresh "
        "skills-vendor/gregoryfoster-skills (gregoryfoster/skills#284)"
    )
    assert "this project tailors .skills/doc-sensitive-paths but not" in source, (
        "the vendored doc-check.sh no longer names a half-tailoring; the note this "
        "file exists to silence is gregoryfoster/skills#284"
    )
