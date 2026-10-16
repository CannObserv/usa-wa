"""Pin every "After editing `deploy/<unit>`" instruction to the copy it needs (issue #509).

Units are installed as root-owned **copies** of their ``deploy/`` counterparts, so
``daemon-reload`` alone re-reads the stale installed copy and deploys nothing. Four of
DEPLOYMENT.md's lifecycle rows and COMMANDS.md's systemd block said exactly that —
reload, restart — and following them looked like a deploy while changing nothing.

Three guards, all pure file parse:

* **Copy before reload.** Every "After editing `deploy/X`" instruction, in any
  operator doc, prescribes ``sudo cp deploy/X /etc/systemd/system/`` — the same
  ``X``, brace form included — ahead of its ``daemon-reload``.
* **Real units.** Each ``X`` expands (``a.{service,timer}`` → two names) to files
  that exist in ``deploy/``, so a renamed unit can't leave a row copying nothing.
* **Every unit has a row.** DEPLOYMENT.md's lifecycle table names every shipped
  ``deploy/*.{service,timer}``: a new unit without a deploy instruction fails here
  rather than waiting for someone to edit it.
"""

import re
from pathlib import Path

import pytest
from systemd_units import DEPLOY

REPO = DEPLOY.parent
DEPLOYMENT_DOC = REPO / "docs" / "DEPLOYMENT.md"
#: Operator docs that give deploy instructions: the repo root's and ``docs/``'s own,
#: not ``docs/plans`` or ``docs/specs``, whose commands are dated records.
OPERATOR_DOCS = sorted([*REPO.glob("*.md"), *(REPO / "docs").glob("*.md")])

# "After editing `deploy/usa-wa-pipeline.{service,timer}`" — backticked in a table
# row, bare in a code-block comment ("# After editing deploy/usa-wa.service: …").
AFTER_EDITING_RE = re.compile(r"After editing `?deploy/(?P<spec>[\w@.{},-]+?)`?(?=[\s:|`]|$)")
BRACE_RE = re.compile(r"^(?P<stem>[^{}]+)\{(?P<alts>[^{}]+)\}$")


def expand(spec: str) -> list[str]:
    """``usa-wa-pipeline.{service,timer}`` → both unit names; a plain name → itself."""
    match = BRACE_RE.match(spec)
    if match is None:
        return [spec]
    return [match["stem"] + alt for alt in match["alts"].split(",")]


def instructions(text: str) -> list[tuple[int, str, str]]:
    """Every (line number, spec, action) an "After editing `deploy/…`" line gives.

    A table row's action is the rest of the row. A code-block comment's is the next
    non-blank line — the command it introduces.
    """
    lines = text.splitlines()
    found = []
    for index, line in enumerate(lines):
        match = AFTER_EDITING_RE.search(line)
        if match is None:
            continue
        if line.lstrip().startswith("|"):
            action = line[match.end() :]
        else:
            action = next((later for later in lines[index + 1 :] if later.strip()), "")
        found.append((index + 1, match["spec"], action))
    return found


def copies_before_reload(spec: str, action: str) -> bool:
    """Whether ``action`` copies ``deploy/<spec>`` into place before it reloads."""
    copy = action.find(f"sudo cp deploy/{spec} /etc/systemd/system/")
    reload = action.find("daemon-reload")
    return copy != -1 and (reload == -1 or copy < reload)


ALL_INSTRUCTIONS = [
    (doc, line, spec, action)
    for doc in OPERATOR_DOCS
    for line, spec, action in instructions(doc.read_text())
]


def instruction_id(case: tuple[Path, int, str, str]) -> str:
    doc, line, _, _ = case
    return f"{doc.relative_to(REPO)}:{line}"


def test_the_scan_finds_the_lifecycle_table_and_the_commands_block():
    """Non-vacuous: a reworded phrase must not silently empty the guard."""
    docs = {doc.relative_to(REPO).as_posix() for doc, _, _, _ in ALL_INSTRUCTIONS}
    assert {"docs/DEPLOYMENT.md", "docs/COMMANDS.md"} <= docs


@pytest.mark.parametrize("case", ALL_INSTRUCTIONS, ids=instruction_id)
def test_every_after_editing_instruction_copies_before_it_reloads(case):
    doc, line, spec, action = case
    assert copies_before_reload(spec, action), (
        f"{doc.relative_to(REPO)}:{line}: after editing deploy/{spec}, the instruction must "
        f"start `sudo cp deploy/{spec} /etc/systemd/system/ &&` — the installed unit is a "
        "copy, so daemon-reload alone re-reads the stale one and deploys nothing"
    )


@pytest.mark.parametrize("case", ALL_INSTRUCTIONS, ids=instruction_id)
def test_every_after_editing_instruction_names_shipped_units(case):
    doc, line, spec, _ = case
    missing = [name for name in expand(spec) if not (DEPLOY / name).is_file()]
    assert not missing, f"{doc.relative_to(REPO)}:{line}: no such unit in deploy/: {missing}"


def test_the_lifecycle_table_covers_every_shipped_unit():
    shipped = {p.name for pattern in ("*.service", "*.timer") for p in DEPLOY.glob(pattern)}
    covered = {
        name for _, spec, _ in instructions(DEPLOYMENT_DOC.read_text()) for name in expand(spec)
    }
    assert shipped - covered == set(), (
        "docs/DEPLOYMENT.md § Lifecycle reference has no `After editing` row for "
        f"{sorted(shipped - covered)} — add one that copies the unit into place"
    )


def test_deployment_doc_names_this_guard():
    assert "test_docs_unit_copy_drift.py" in DEPLOYMENT_DOC.read_text()


@pytest.mark.parametrize(
    ("spec", "action", "ok"),
    [
        (
            "a.service",
            " | `sudo cp deploy/a.service /etc/systemd/system/ && sudo systemctl daemon-reload` |",
            True,
        ),
        ("a.service", " | `sudo systemctl daemon-reload && sudo systemctl restart a` |", False),
        # The copy has to come first: copying after the reload deploys nothing either.
        (
            "a.service",
            "sudo systemctl daemon-reload && sudo cp deploy/a.service /etc/systemd/system/",
            False,
        ),
        # Copying a different unit than the one edited doesn't count.
        (
            "a.{service,timer}",
            "sudo cp deploy/a.service /etc/systemd/system/ && sudo systemctl daemon-reload",
            False,
        ),
    ],
)
def test_copies_before_reload(spec, action, ok):
    assert copies_before_reload(spec, action) is ok


def test_instructions_reads_a_table_row_and_a_code_block_comment():
    text = (
        "| After editing `deploy/a.{service,timer}` | `sudo cp …` |\n"
        "```bash\n"
        "# After editing deploy/b.service: reload then restart\n"
        "\n"
        "sudo systemctl daemon-reload\n"
        "```\n"
    )
    assert instructions(text) == [
        (1, "a.{service,timer}", " | `sudo cp …` |"),
        (3, "b.service", "sudo systemctl daemon-reload"),
    ]


def test_expand():
    assert expand("a.{service,timer}") == ["a.service", "a.timer"]
    assert expand("usa-wa-notify-failure@.service") == ["usa-wa-notify-failure@.service"]
