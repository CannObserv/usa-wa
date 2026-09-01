"""The `TEST_DATABASE_URL is not set` remediation must name a real path (#296).

``conftest_db.py`` raises when a db-marked test resolves ``test_engine`` with no
DSN, and the message it raised told the reader to run::

    export $(cat /etc/usa-wa/.env .env 2>/dev/null | xargs)

Two things were wrong with that in the checkout AGENTS.md § Server Lifecycle
*mandates* feature work happen in (#87). ``.env`` is git-ignored, so a worktree
never has one — the advice expanded to ``/etc/usa-wa/.env`` alone, which by the
repo's deliberate secrets split (AGENTS.md § Environment Variables) is the one
file that does **not** carry ``TEST_DATABASE_URL``. Following it changed
nothing, and the same error repeated.

And the escape hatch it offered, ``-m 'not db'``, is the spelling AGENTS.md
warns against: ``-m`` is last-wins against ``addopts``, so the bare form
silently re-enables the integration tier the default run excludes.

These tests pin the message against both. ``scripts/pre-ship.sh`` refuses before
pytest starts, so the ship gate no longer reaches this path at all — but a plain
``uv run pytest`` in a worktree still does, and that is the reader this message
exists for.
"""

import subprocess
from pathlib import Path

import conftest_db

GIT_ENV = {
    "PATH": "/usr/bin:/bin:/usr/local/bin",
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def _repo(tmp_path: Path) -> Path:
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, env=GIT_ENV)
    (tmp_path / ".gitignore").write_text(".env\n.worktrees/\n")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, env=GIT_ENV)
    subprocess.run(
        ["git", "commit", "-qm", "seed", "--no-verify"], cwd=tmp_path, check=True, env=GIT_ENV
    )
    return tmp_path


def _worktree(repo: Path) -> Path:
    wt = repo / ".worktrees" / "wt"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "wt", str(wt)],
        cwd=repo,
        check=True,
        env=GIT_ENV,
    )
    return wt


def test_names_this_checkouts_env_when_it_has_one(tmp_path):
    repo = _repo(tmp_path)
    (repo / ".env").write_text("TEST_DATABASE_URL=postgresql://stub/testdb\n")

    message = conftest_db.missing_test_database_url_message(repo)

    assert str(repo / ".env") in message


def test_names_the_primary_checkouts_env_from_a_worktree(tmp_path):
    """The #296 case: no `.env` here, and the old advice named only this one."""
    repo = _repo(tmp_path)
    (repo / ".env").write_text("TEST_DATABASE_URL=postgresql://stub/testdb\n")
    wt = _worktree(repo)

    message = conftest_db.missing_test_database_url_message(wt)

    assert str(repo / ".env") in message
    assert str(wt / ".env") not in message


def test_no_env_anywhere_says_to_create_one(tmp_path):
    """The degenerate case must not fall back to the unfollowable recipe.

    With no `.env` in either checkout the old shape reduced to `cat
    /etc/usa-wa/.env`, which by the repo's deliberate split is the one file that
    never carries the variable — #296's "the suggested fix cannot work",
    surviving inside the change that fixed it. A fresh clone needs to be told to
    CREATE the file, not to read one that cannot help.
    """
    message = conftest_db.missing_test_database_url_message(tmp_path)

    assert str(tmp_path / ".env") in message
    assert "/etc/usa-wa/.env" not in message


def test_the_unit_tier_hatch_spells_out_both_markers():
    """`-m` is last-wins against addopts; the bare `not db` re-enables integration."""
    message = conftest_db.missing_test_database_url_message(Path("/nonexistent"))

    assert "not db and not integration" in message
