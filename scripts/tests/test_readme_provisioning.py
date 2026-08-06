"""Assert README's fresh-host provisioning block covers every shipped timer (issue #167).

The ratchet argument that closed #51/#52, applied to docs: `deploy/` shipped 11
timers while README's `### Scheduled units` block enabled 5, so a host provisioned
from README came up with four of the daily invariant gates silently absent —
nothing fails, nothing alerts, and the absence looks identical to "no drift".

Three assertions, all cross-checked against `deploy/*.timer` itself (never against
another doc's copy of the table):

1. every shipped timer appears in the enable block, and nothing else does;
2. each entry's cadence comment matches that timer's own `OnCalendar=`;
3. the `## Deploy` preamble's "<N> timer-driven oneshots" count matches.

Pure file parse — no DB, no systemd. Shares the unit parser with
``test_unit_ordering`` so the two can't disagree about what a unit says.
"""

import re

import pytest
from test_unit_ordering import DEPLOY, unit_value

README = DEPLOY.parent / "README.md"

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


def shipped_timers() -> set[str]:
    return {p.name for p in DEPLOY.glob("*.timer")}


def schedule(timer: str) -> tuple[str | None, str, str]:
    """Parse a timer's own OnCalendar= into (weekday or None, HH, MM)."""
    on_calendar = unit_value(DEPLOY / timer, "Timer", "OnCalendar")
    assert on_calendar is not None, f"{timer} has no OnCalendar="
    match = ONCALENDAR_RE.match(on_calendar)
    assert match, f"{timer}: unhandled OnCalendar form {on_calendar!r}"
    return match["weekday"], match["hh"], match["mm"]


def cadence_phrase(timer: str) -> str:
    """Render a timer's OnCalendar= as the phrase README's comment must open with."""
    weekday, hh, mm = schedule(timer)
    return f"weekly {weekday} {hh}:{mm} UTC" if weekday else f"daily {hh}:{mm} UTC"


def enable_block() -> dict[str, str]:
    """Parse README's `### Scheduled units` snippet → {timer unit: trailing comment}.

    Ordered by appearance (dicts preserve insertion order) so the ordering
    assertion can read the sequence off the same parse.
    """
    lines = README.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("### Scheduled units"))
    entries: dict[str, str] = {}
    for line in lines[start:]:
        match = ENABLE_RE.match(line)
        if not match:
            continue
        for unit in match["units"].split():
            entries[unit] = (match["note"] or "").strip()
    return entries


def test_every_shipped_timer_is_enabled_by_the_readme():
    """A new deploy/*.timer must be added to the provisioning block (the #167 ratchet)."""
    assert set(enable_block()) == shipped_timers()


@pytest.mark.parametrize("timer", sorted(shipped_timers()))
def test_enable_comment_states_the_units_own_cadence(timer):
    """Each entry's comment opens with the cadence parsed from that timer's OnCalendar=."""
    note = enable_block()[timer]
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
