"""The SocratiCode client key file stays out of git (#556).

``.claude/settings.local.json`` holds this host's ``QDRANT_API_KEY`` for the
cohort's shared ``co-index`` store. Qdrant has one global key, so a leak from
any repo means rotating it on every cohort host at once, with no overlap
window — and this repo is public. CannObserv/index's ``install_qdrant_key.sh``
refuses a target where ``git check-ignore`` prints nothing; these tests keep
that precondition from regressing.

Both probes ask git rather than reading ``.gitignore``: a rule can be shadowed
by a later negation, and a tracked file is committed whatever the rules say.
"""

import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: The file holding this host's QDRANT_API_KEY. Never tracked, always ignored.
SETTINGS_LOCAL = ".claude/settings.local.json"

#: Tracked and not ignored — the negative control for both probes.
TRACKED_CONTROL = ".claude/settings.json"


def _git(*args: str) -> subprocess.CompletedProcess:
    """Run git in the repo root; the caller reads the exit code."""
    return subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, check=False
    )


def _tracked(path: str) -> bool:
    return _git("ls-files", "--error-unmatch", path).returncode == 0


def _ignored(path: str) -> bool:
    return _git("check-ignore", "-q", path).returncode == 0


def test_settings_local_is_not_tracked() -> None:
    """Tracked beats ignored: a file already in the index is committed regardless.

    The remedy is then a key rotation, not a ``.gitignore`` edit.
    """
    assert not _tracked(SETTINGS_LOCAL), (
        f"{SETTINGS_LOCAL} is tracked: the key is in history — rotate it cohort-wide, "
        f"then `git rm --cached {SETTINGS_LOCAL}`"
    )


def test_settings_local_is_git_ignored() -> None:
    """The key install script's own guard: ``git check-ignore`` must print a rule."""
    assert _ignored(SETTINGS_LOCAL), f"{SETTINGS_LOCAL} is not git-ignored: it would be committed"


def test_the_probes_answer() -> None:
    """A tracked, unignored control flips both probes, so neither passes vacuously."""
    assert _tracked(TRACKED_CONTROL), f"{TRACKED_CONTROL} reads untracked: tracked probe is dead"
    assert not _ignored(TRACKED_CONTROL), f"{TRACKED_CONTROL} reads ignored: ignore probe is dead"
