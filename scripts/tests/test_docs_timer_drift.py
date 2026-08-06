"""Pin the docs that restate `deploy/*.timer` against the units themselves (issue #167).

The ratchet argument that closed #51/#52, applied to docs: `deploy/` shipped 11
timers while README's `### Scheduled units` block enabled 5, so a host provisioned
from README came up with four of the daily invariant gates silently absent —
nothing fails, nothing alerts, and the absence looks identical to "no drift".

Two docs restate the timer set, and both are pinned here against the unit files —
never against each other's copy (#167 CR, finding 6):

* **README** `### Scheduled units` — the fresh-host provisioning block. Must
  enable every shipped timer, exactly those, comment each with that timer's own
  cadence, and stay ordered by next-elapse.
* **docs/DEPLOYMENT.md** `## Services` — the operator's what-each-one-does table.
  Every shipped timer needs a row, and each row's cadence must match the unit.

Both docs must also name this module, so the pointer survives a rename.

Pure file parse — no DB, no systemd. The unit parser is shared with
``test_unit_ordering`` via ``systemd_units`` so the two can't disagree.
"""

import re
from pathlib import Path

import pytest
from systemd_units import DEPLOY, unit_values

README = DEPLOY.parent / "README.md"
DEPLOYMENT_DOC = DEPLOY.parent / "docs" / "DEPLOYMENT.md"

# Spelled-out counts we accept in the "<N> timer-driven oneshots" preamble.
NUMBER_WORDS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}

ENABLE_RE = re.compile(
    r"^\s*sudo systemctl enable --now\s+(?P<units>[^#]+?)\s*(?:#\s*(?P<note>.*))?$"
)
ONCALENDAR_RE = re.compile(
    r"^(?:(?P<weekday>[A-Za-z]{3})\s+)?\*-\*-\*\s+(?P<hh>\d{2}):(?P<mm>\d{2}):\d{2}\s+UTC$"
)
COUNT_RE = re.compile(r"(\w+)\s+timer-driven oneshots")
# A cadence as the docs write it: "06:00 UTC" or "Sun 07:45 UTC".
PROSE_CADENCE_RE = re.compile(r"(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun)\s+)?(\d{2}):(\d{2})\s+UTC")
TIMER_MENTION_RE = re.compile(r"usa-wa-[\w-]+\.timer")


def shipped_timers() -> set[str]:
    return {p.name for p in DEPLOY.glob("*.timer")}


def schedule(timer: str) -> tuple[str | None, str, str]:
    """Parse a timer's own OnCalendar= into (weekday or None, HH, MM).

    ``OnCalendar=`` is *additive*: repeated lines each add an elapse expression
    (and a bare ``OnCalendar=`` resets the list), so a multi-schedule timer can't
    be summarized by one doc comment. Fail loudly rather than silently pinning the
    docs to whichever line happens to come last (#167 CR, finding 1).
    """
    values = unit_values(DEPLOY / timer, "Timer", "OnCalendar")
    assert len(values) == 1, (
        f"{timer}: {len(values)} OnCalendar= lines — the docs state one cadence per "
        f"timer; extend the renderer (and the docs) before shipping a multi-schedule timer"
    )
    match = ONCALENDAR_RE.match(values[0])
    assert match, f"{timer}: unhandled OnCalendar form {values[0]!r} — extend ONCALENDAR_RE"
    return match["weekday"], match["hh"], match["mm"]


def cadence_phrase(timer: str) -> str:
    """Render a timer's OnCalendar= as the phrase README's comment must open with."""
    weekday, hh, mm = schedule(timer)
    return f"weekly {weekday} {hh}:{mm} UTC" if weekday else f"daily {hh}:{mm} UTC"


def section_lines(path: Path, heading: str) -> list[str]:
    """Lines under `heading`, stopping at the next heading of the same or higher level.

    Bounding matters: scanning to EOF would swallow a `systemctl enable` example
    from any section appended later and blame the wrong block (#167 CR, finding 2).

    Fenced code is tracked, because the sections being parsed are mostly shell
    snippets whose `# Ingest (daily)` comments are indistinguishable from an H1
    on a line-shape test alone.
    """
    level = len(heading) - len(heading.lstrip("#"))
    lines = path.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    out: list[str] = []
    fenced = False
    for line in lines[start + 1 :]:
        if line.lstrip().startswith("```"):
            fenced = not fenced
        depth = len(line) - len(line.lstrip("#"))
        if not fenced and 0 < depth <= level:
            break
        out.append(line)
    return out


def enable_block() -> dict[str, str]:
    """Parse README's `### Scheduled units` snippet → {enabled unit: trailing comment}.

    Ordered by appearance (dicts preserve insertion order) so the ordering
    assertion can read the sequence off the same parse. Units are captured as
    written — a `.timer` suffix left off (which would enable the `.service`
    instead) must fail, not be silently normalized.
    """
    entries: dict[str, str] = {}
    for line in section_lines(README, "### Scheduled units"):
        match = ENABLE_RE.match(line)
        if not match:
            continue
        for unit in match["units"].split():
            entries[unit] = (match["note"] or "").strip()
    return entries


def deployment_rows() -> dict[str, str]:
    """Parse docs/DEPLOYMENT.md's `## Services` table → {timer unit: its row}."""
    rows: dict[str, str] = {}
    for line in section_lines(DEPLOYMENT_DOC, "## Services"):
        for timer in TIMER_MENTION_RE.findall(line):
            rows[timer] = line
    return rows


def test_every_shipped_timer_is_enabled_by_the_readme():
    """A new deploy/*.timer must be added to the provisioning block (the #167 ratchet)."""
    assert set(enable_block()) == shipped_timers()


@pytest.mark.parametrize("timer", sorted(shipped_timers()))
def test_enable_comment_states_the_units_own_cadence(timer):
    """Each entry's comment opens with the cadence parsed from that timer's OnCalendar=."""
    note = enable_block().get(timer, "")
    expected = cadence_phrase(timer)
    assert note.startswith(expected), f"{timer}: comment {note!r} does not open with {expected!r}"


def test_enable_block_is_ordered_by_next_elapse():
    """Dailies before weeklies, each group by clock time — the order an operator provisions in."""
    keyed = [(bool(w), h, m) for w, h, m in (schedule(t) for t in enable_block())]
    assert keyed == sorted(keyed)


def test_deploy_preamble_states_the_shipped_timer_count():
    """The `## Deploy` prose count tracks deploy/*.timer (it read "two" against 11 in #167)."""
    counts = [
        NUMBER_WORDS[word.lower()] if word.lower() in NUMBER_WORDS else int(word)
        for word in COUNT_RE.findall(README.read_text())
        if word.lower() in NUMBER_WORDS or word.isdigit()
    ]
    assert counts, "README states no count of timer-driven oneshots"
    assert set(counts) == {len(shipped_timers())}


def test_every_shipped_timer_has_a_deployment_table_row():
    """docs/DEPLOYMENT.md § Services describes every timer the deploy ships."""
    assert set(deployment_rows()) == shipped_timers()


@pytest.mark.parametrize("timer", sorted(shipped_timers()))
def test_deployment_table_cadence_matches_the_unit(timer):
    """Each § Services row states exactly one cadence, and it's that timer's own."""
    row = deployment_rows().get(timer, "")
    found = PROSE_CADENCE_RE.findall(row)
    assert len(found) == 1, f"{timer}: expected one cadence in its § Services row, found {found}"
    weekday, hh, mm = found[0]
    assert (weekday or None, hh, mm) == schedule(timer)


@pytest.mark.parametrize("doc", [README, DEPLOYMENT_DOC], ids=["README", "DEPLOYMENT"])
def test_pinned_docs_name_this_guard(doc):
    """Both docs point at this module, so a rename can't leave a dangling pointer."""
    assert Path(__file__).name in doc.read_text()
