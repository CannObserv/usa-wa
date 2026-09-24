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
    r"^(?:(?P<weekday>[A-Za-z]{3})\s+)?\*-\*-(?P<day>\*|\d{2})\s+"
    r"(?P<hh>\d{2}):(?P<mm>\d{2}):\d{2}\s+UTC$"
)
#: The last day every month has. A later one (``*-*-31``) silently skips the short months.
LAST_UNIVERSAL_DAY = 28
COUNT_RE = re.compile(r"(\w+)\s+timer-driven oneshots")
# A cadence as the docs write it: "06:00 UTC", "Sun 07:45 UTC" or "1st 09:00 UTC" (#237).
PROSE_CADENCE_RE = re.compile(
    r"(?:(Mon|Tue|Wed|Thu|Fri|Sat|Sun|\d{1,2}(?:st|nd|rd|th))\s+)?(\d{2}):(\d{2})\s+UTC"
)
TIMER_MENTION_RE = re.compile(r"usa-wa-[\w-]+\.timer")


def shipped_timers() -> set[str]:
    return {p.name for p in DEPLOY.glob("*.timer")}


def ordinal(day: int) -> str:
    """How the docs write a monthly timer's day: 1 → ``1st``, 12 → ``12th``, 22 → ``22nd``."""
    suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
    return f"{day}{suffix}"


def parse_on_calendar(value: str, unit: str = "OnCalendar") -> tuple[str | None, str, str]:
    """Parse one OnCalendar= value into (qualifier, HH, MM).

    The qualifier is what the docs write before the clock time: ``None`` for a daily
    timer, the weekday (``Sun``) for a weekly one, the ordinal day (``1st``) for a
    monthly one (#237). Two monthly shapes are refused rather than rendered, because
    each fires on fewer occasions than any phrase for it would claim.
    """
    match = ONCALENDAR_RE.match(value)
    assert match, f"{unit}: unhandled OnCalendar form {value!r} — extend ONCALENDAR_RE"
    weekday, day = match["weekday"], match["day"]
    if day == "*":
        return weekday, match["hh"], match["mm"]
    assert weekday is None, (
        f"{unit}: {value!r} fires only on a {weekday} that falls on day {day} — "
        "neither weekly nor monthly, so no cadence the docs state would be true"
    )
    assert int(day) <= LAST_UNIVERSAL_DAY, (
        f"{unit}: {value!r} skips every month without a day {day}, yet the docs would call "
        f"it monthly — schedule it on day {LAST_UNIVERSAL_DAY} or earlier"
    )
    return ordinal(int(day)), match["hh"], match["mm"]


def period(qualifier: str | None) -> str:
    """``daily``, ``weekly`` or ``monthly`` — read off the qualifier's shape."""
    if qualifier is None:
        return "daily"
    return "monthly" if qualifier[0].isdigit() else "weekly"


#: Provisioning order: every daily, then every weekly, then every monthly.
PERIOD_RANK = {"daily": 0, "weekly": 1, "monthly": 2}


def schedule(timer: str) -> tuple[str | None, str, str]:
    """Parse a timer's own OnCalendar= into (qualifier, HH, MM) — see ``parse_on_calendar``.

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
    return parse_on_calendar(values[0], timer)


def render_cadence(qualifier: str | None, hh: str, mm: str) -> str:
    """The phrase README's comment must open with: ``daily 06:00 UTC``, ``weekly Sun …``,
    ``monthly 1st …``."""
    when = f"{hh}:{mm} UTC"
    return f"{period(qualifier)} {qualifier} {when}" if qualifier else f"daily {when}"


def cadence_phrase(timer: str) -> str:
    """Render a timer's OnCalendar= as the phrase README's comment must open with."""
    return render_cadence(*schedule(timer))


def section_lines(path: Path, heading: str) -> list[str]:
    """Lines under `heading`, stopping at the next heading of the same or higher level.

    Bounding matters: scanning to EOF would swallow a `systemctl enable` example
    from any section appended later and blame the wrong block (#167 CR, finding 2).

    Fenced code is tracked, because the sections being parsed are mostly shell
    snippets whose `# Ingest (daily)` comments are indistinguishable from an H1
    on a line-shape test alone. Both ways this can go wrong are asserted rather
    than absorbed (#167 CR round 2): a heading that has been renamed says so
    instead of raising a bare StopIteration (finding 9), and an unbalanced fence
    fails instead of silently swallowing the rest of the file (finding 13).
    """
    level = len(heading) - len(heading.lstrip("#"))
    lines = path.read_text().splitlines()
    starts = [i for i, line in enumerate(lines) if line.strip() == heading]
    assert starts, f"{path.name}: no {heading!r} heading — this guard is pinned to it"
    assert len(starts) == 1, (
        f"{path.name}: {len(starts)} {heading!r} headings — the guard would parse only the "
        f"first and the rest would go unchecked"
    )
    out: list[str] = []
    fenced = False
    for line in lines[starts[0] + 1 :]:
        if line.lstrip().startswith("```"):
            fenced = not fenced
        depth = len(line) - len(line.lstrip("#"))
        if not fenced and 0 < depth <= level:
            return out
        out.append(line)
    assert not fenced, f"{path.name}: unbalanced code fence under {heading!r} — section bound lost"
    return out


def fenced_block(lines: list[str]) -> list[str]:
    """The contents of the first ```-fenced block in `lines`.

    The provisioning block is the fenced snippet, not the whole section: prose
    around it may legitimately quote a `systemctl enable` line without that being
    a command the operator is meant to run (#167 CR round 3, finding 15a).
    """
    opened = False
    out: list[str] = []
    for line in lines:
        if line.lstrip().startswith("```"):
            if opened:
                return out
            opened = True
            continue
        if opened:
            out.append(line)
    raise AssertionError("no fenced block found — the guard is pinned to the fenced snippet")


def enable_block() -> dict[str, str]:
    """Parse README's `### Scheduled units` snippet → {enabled unit: trailing comment}.

    Ordered by appearance (dicts preserve insertion order) so the ordering
    assertion can read the sequence off the same parse. Units are captured as
    written — a `.timer` suffix left off (which would enable the `.service`
    instead) must fail, not be silently normalized.
    """
    entries: dict[str, str] = {}
    for line in fenced_block(section_lines(README, "### Scheduled units")):
        match = ENABLE_RE.match(line)
        if not match:
            continue
        for unit in match["units"].split():
            entries[unit] = (match["note"] or "").strip()
    return entries


def timer_rows(lines: list[str]) -> dict[str, str]:
    """Map each timer named in a **table row** of `lines` to that row.

    Table rows only: prose in the same section may name a timer (a runbook aside,
    a note about a retired unit) without being a cadence claim, and treating that
    as a row made the guard reject ordinary doc edits (#167 CR round 3, finding
    15). Two rows for one timer are still refused — the second copy of a cadence
    is exactly the drift this exists to catch (round 2, finding 14).
    """
    rows: dict[str, str] = {}
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        for timer in TIMER_MENTION_RE.findall(line):
            assert timer not in rows, (
                f"{timer}: named in two table rows — the second cadence would go unchecked; "
                f"keep one row per timer"
            )
            rows[timer] = line
    return rows


def deployment_rows() -> dict[str, str]:
    """Parse docs/DEPLOYMENT.md's `## Services` table → {timer unit: its row}."""
    return timer_rows(section_lines(DEPLOYMENT_DOC, "## Services"))


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
    """Dailies, then weeklies, then monthlies, each group by clock time — the order an
    operator provisions in."""
    keyed = [(PERIOD_RANK[period(q)], h, m) for q, h, m in (schedule(t) for t in enable_block())]
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
    qualifier, hh, mm = found[0]
    assert (qualifier or None, hh, mm) == schedule(timer)


@pytest.mark.parametrize("doc", [README, DEPLOYMENT_DOC], ids=["README", "DEPLOYMENT"])
def test_pinned_docs_name_this_guard(doc):
    """Both docs point at this module, so a rename can't leave a dangling pointer."""
    assert Path(__file__).name in doc.read_text()


# --- parser has-teeth proofs (#167 CR round 2) -------------------------------
# The guard's own failure modes: each of these silently widened or crashed the
# parse before round 2, so they assert the diagnostics, not just the behaviour.


def test_section_lines_names_a_heading_it_cannot_find(tmp_path):
    """A renamed heading must say so, not raise a bare StopIteration (finding 9)."""
    doc = tmp_path / "doc.md"
    doc.write_text("## Something Else\n\ntext\n")
    with pytest.raises(AssertionError, match="## Services"):
        section_lines(doc, "## Services")


def test_section_lines_rejects_an_unbalanced_fence(tmp_path):
    """An unclosed fence would silently restore the EOF scan finding 2 removed (finding 13)."""
    doc = tmp_path / "doc.md"
    doc.write_text("## A\n\n```bash\necho hi\n\n## B\n\nlater\n")
    with pytest.raises(AssertionError, match="unbalanced"):
        section_lines(doc, "## A")


def test_section_lines_bounds_the_section_but_not_shell_comments(tmp_path):
    """Stops at the next same-level heading; `# comment` inside a fence is not a heading."""
    doc = tmp_path / "doc.md"
    doc.write_text("## A\n\n```bash\n# Ingest (daily)\necho hi\n```\n\n## B\n\nlater\n")
    assert section_lines(doc, "## A") == ["", "```bash", "# Ingest (daily)", "echo hi", "```", ""]


def test_section_lines_keeps_deeper_headings(tmp_path):
    """A deeper heading is part of the section, not its end."""
    doc = tmp_path / "doc.md"
    doc.write_text("## A\n\n### A.1\n\ninner\n\n## B\n\nlater\n")
    assert "### A.1" in section_lines(doc, "## A")
    assert "later" not in section_lines(doc, "## A")


def test_section_lines_rejects_a_duplicated_heading(tmp_path):
    """Two identical headings: the guard would parse only the first (finding 16)."""
    doc = tmp_path / "doc.md"
    doc.write_text("## Services\n\nfirst\n\n## Services\n\nsecond\n")
    with pytest.raises(AssertionError, match="2 '## Services' headings"):
        section_lines(doc, "## Services")


def test_fenced_block_returns_the_first_block_and_ignores_later_prose():
    """Only the fenced snippet is the provisioning block; prose below it isn't (finding 15a)."""
    lines = [
        "prose before",
        "```bash",
        "sudo systemctl enable --now usa-wa-a.timer  # daily 01:00 UTC",
        "```",
        "prose after mentioning: sudo systemctl enable --now usa-wa-b.timer",
    ]
    assert fenced_block(lines) == ["sudo systemctl enable --now usa-wa-a.timer  # daily 01:00 UTC"]


def test_fenced_block_requires_a_block():
    """A section that lost its fence must say so, not parse as empty."""
    with pytest.raises(AssertionError, match="no fenced block"):
        fenced_block(["just prose", "no fence here"])


def test_timer_rows_ignores_prose_mentions():
    """Only table rows carry cadences; prose naming a timer is not a row (finding 15)."""
    lines = [
        "| WSL refresh | … `usa-wa-wsl-refresh.timer` …; 06:00 UTC |",
        "Note: run `usa-wa-wsl-refresh.timer` by hand after a backfill.",
        "Retired in #99: `usa-wa-old-thing.timer`.",
    ]
    assert sorted(timer_rows(lines)) == ["usa-wa-wsl-refresh.timer"]


def test_timer_rows_rejects_a_second_row_for_one_timer():
    """Two rows for one timer leave the first unchecked — the copy problem again (finding 14)."""
    rows = [
        "| WSL refresh | … `usa-wa-wsl-refresh.timer` …; 06:00 UTC |",
        "| See also | … `usa-wa-wsl-refresh.timer` …; 09:00 UTC |",
    ]
    with pytest.raises(AssertionError, match="usa-wa-wsl-refresh.timer"):
        timer_rows(rows)


# --- monthly cadence (#237) --------------------------------------------------
# The roster edition re-check is the first timer that is neither daily nor weekly. The parser
# learns the one monthly shape it ships (``*-*-DD``) and refuses the two that would let a doc
# state a cadence the unit does not keep.


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("*-*-* 06:00:00 UTC", (None, "06", "00")),
        ("Sun *-*-* 08:00:00 UTC", ("Sun", "08", "00")),
        ("*-*-01 09:00:00 UTC", ("1st", "09", "00")),
        ("*-*-15 23:30:00 UTC", ("15th", "23", "30")),
    ],
)
def test_parse_on_calendar_reads_daily_weekly_and_monthly(value, expected):
    """The qualifier is what the docs write before the clock: none, a weekday, an ordinal day."""
    assert parse_on_calendar(value) == expected


@pytest.mark.parametrize(
    ("qualifier", "phrase"),
    [
        (None, "daily 06:00 UTC"),
        ("Sun", "weekly Sun 06:00 UTC"),
        ("1st", "monthly 1st 06:00 UTC"),
    ],
)
def test_render_cadence_names_the_period(qualifier, phrase):
    """README's comment opens with the period, so a monthly unit can't read as a daily one."""
    assert render_cadence(qualifier, "06", "00") == phrase


@pytest.mark.parametrize(
    ("day", "rendered"),
    [
        (1, "1st"),
        (2, "2nd"),
        (3, "3rd"),
        (4, "4th"),
        (11, "11th"),
        (12, "12th"),
        (13, "13th"),
        (21, "21st"),
        (22, "22nd"),
        (23, "23rd"),
        (28, "28th"),
    ],
)
def test_ordinal_day_suffixes(day, rendered):
    """11-13 take "th" — the case a last-digit lookup alone gets wrong."""
    assert ordinal(day) == rendered


def test_parse_on_calendar_rejects_a_weekday_and_a_day_together():
    """``Sun *-*-01`` fires only on a 1st that is a Sunday: neither weekly nor monthly."""
    with pytest.raises(AssertionError, match="neither weekly nor monthly"):
        parse_on_calendar("Sun *-*-01 09:00:00 UTC")


def test_parse_on_calendar_rejects_a_day_that_skips_short_months():
    """``*-*-31`` silently skips the five shorter months; the docs would still say "monthly"."""
    with pytest.raises(AssertionError, match="skips"):
        parse_on_calendar("*-*-31 09:00:00 UTC")


def test_prose_cadence_reads_an_ordinal_day():
    """The § Services row states a monthly cadence as "1st 09:00 UTC"."""
    assert PROSE_CADENCE_RE.findall("(`x.timer` → `.service`; 1st 09:00 UTC, #237)") == [
        ("1st", "09", "00")
    ]
