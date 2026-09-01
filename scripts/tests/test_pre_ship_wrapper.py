"""Tests for scripts/pre-ship.sh — the project-local env-loading ship gate (#172).

The vendored gate ships without env loading by design, and names the override
point in a comment. `usa-wa` needs it: the gate runs the *full* suite, whose
db-marked majority needs ``TEST_DATABASE_URL``, so on a clean shell the headline
test phase died wholesale — a gate failing for non-code reasons. (Before #185 it
died at import: ``conftest.py`` raised before a single test was collected.)

The wrapper is not a fork. It loads the two env files and ``exec``s the vendored
script, so upstream fixes keep landing without a merge. Three properties matter
and each is asserted below:

  * the delegate sees ``TEST_DATABASE_URL`` even when the caller's shell lacks it
    (the whole point);
  * the delegate's exit code reaches the caller unchanged — the shipping skill's
    Iron Law gates on it, so a swallowed failure would silently green-light a
    broken push;
  * a missing delegate is an actionable error, not bash's bare "No such file or
    directory". Worktrees don't populate submodules, and AGENTS.md mandates
    worktree-based feature work (#87), so this fires routinely.

Tests 1-3 run the wrapper against a *stub* delegate planted in a scratch repo:
fast, and no gate-bypass seam has to be added to production code to make the
delegate swappable. That leaves the real delegate path unverified, which is
exactly what test 4 covers.
"""

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent.parent / "pre-ship.sh"  # scripts/tests/ → scripts/

DELEGATE_REL = "skills/shipping-work-python-fastapi/scripts/pre-ship.sh"

# Records the delegate's view of the environment, then exits with a caller-chosen
# code. `$@` is captured too so argument pass-through stays observable.
STUB = """\
#!/usr/bin/env bash
printf 'TEST_DATABASE_URL=%s\\n' "${TEST_DATABASE_URL:-<unset>}" >"$STUB_LOG"
printf 'SYSTEM_ONLY=%s\\n' "${SYSTEM_ONLY:-<unset>}" >>"$STUB_LOG"
printf 'ARGS=%s\\n' "$*" >>"$STUB_LOG"
exit "${STUB_EXIT:-0}"
"""


def _scratch_repo(tmp_path: Path, *, with_delegate: bool = True) -> Path:
    """A git repo holding the wrapper, a repo-root .env, and (optionally) a stub."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    (tmp_path / "scripts").mkdir()
    wrapper = tmp_path / "scripts" / "pre-ship.sh"
    wrapper.write_bytes(SCRIPT.read_bytes())
    wrapper.chmod(0o755)

    # The value the wrapper must surface to the delegate. Not a real DSN — the
    # stub never connects; only the export path is under test.
    (tmp_path / ".env").write_text("TEST_DATABASE_URL=postgresql://stub/testdb\n")
    # git-ignored, exactly as in the real repo. This is the whole of #296: an
    # untracked file cannot travel to a worktree, so a scratch repo that
    # committed its .env would hand every worktree test a free pass.
    (tmp_path / ".gitignore").write_text(".env\n.worktrees/\n")

    if with_delegate:
        delegate = tmp_path / DELEGATE_REL
        delegate.parent.mkdir(parents=True)
        delegate.write_text(STUB)
        delegate.chmod(0o755)

    return tmp_path


def _run(repo: Path, *args: str, env_extra: dict | None = None):
    # Deliberately *not* inheriting os.environ: the clean-shell case is the bug.
    # PATH alone keeps git/bash resolvable.
    #
    # PRE_SHIP_SYSTEM_ENV points at nothing unless a test says otherwise. The
    # real default is /etc/usa-wa/.env, a host file this suite neither owns nor
    # can predict — and one test asserts what happens when *no* env file carries
    # TEST_DATABASE_URL, which a machine that happened to put it there would
    # silently turn green.
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "PRE_SHIP_SYSTEM_ENV": str(repo / "no-such-system.env"),
    }
    env.update(env_extra or {})
    return subprocess.run(
        ["bash", str(repo / "scripts" / "pre-ship.sh"), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        env=env,
    )


def test_delegate_sees_test_database_url_on_a_clean_shell(tmp_path):
    """The regression #172 filed: caller has no TEST_DATABASE_URL, delegate does."""
    repo = _scratch_repo(tmp_path)
    log = tmp_path / "stub.log"

    result = _run(repo, env_extra={"STUB_LOG": str(log)})

    assert result.returncode == 0, result.stderr
    assert "TEST_DATABASE_URL=postgresql://stub/testdb" in log.read_text()


def test_arguments_reach_the_delegate(tmp_path):
    """`"$@"` pass-through — without it `--help` dies inside the wrapper."""
    repo = _scratch_repo(tmp_path)
    log = tmp_path / "stub.log"

    _run(repo, "--help", env_extra={"STUB_LOG": str(log)})

    assert "ARGS=--help" in log.read_text()


def test_delegate_failure_propagates(tmp_path):
    """`exec` keeps the exit code the shipping skill's Iron Law reads."""
    repo = _scratch_repo(tmp_path)

    result = _run(repo, env_extra={"STUB_LOG": str(tmp_path / "stub.log"), "STUB_EXIT": "7"})

    assert result.returncode == 7


def test_missing_delegate_is_an_actionable_error(tmp_path):
    """Unpopulated submodule → exit 2 (upstream's tooling-failure code) + a remedy."""
    repo = _scratch_repo(tmp_path, with_delegate=False)

    result = _run(repo, env_extra={"STUB_LOG": str(tmp_path / "stub.log")})

    assert result.returncode == 2
    assert DELEGATE_REL in result.stderr
    assert "submodule update" in result.stderr


def test_real_delegate_path_resolves():
    """The scratch-repo tests plant their own stub, so nothing above would notice
    the vendored script moving. Pin the hardcoded path against vendor drift."""
    delegate = SCRIPT.parent.parent / DELEGATE_REL
    assert delegate.is_file(), f"vendored gate missing at {DELEGATE_REL}"


# --- #296: the env file a worktree never has ---------------------------------
# `.env` is git-ignored, so `git worktree add` never produces one and nothing in
# worktree-create.sh seeds it. AGENTS.md § Server Lifecycle *mandates* worktree
# feature work (#87), so the checkout the repo tells you to work in was the one
# where the gate died ~60s later in conftest_db.py — and the remediation that
# error printed named the same absent file, so following it changed nothing.


def _commit_all(repo: Path) -> None:
    """A worktree needs a commit to branch from; scratch repos have no identity."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@example.invalid",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@example.invalid",
    }
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, env=env)
    subprocess.run(["git", "commit", "-qm", "seed", "--no-verify"], cwd=repo, check=True, env=env)


def _worktree_of(repo: Path, name: str) -> Path:
    """A second checkout of `repo`, with no `.env` — the #296 state, reproduced.

    Built with the real `git worktree add`, not a hand-made directory: the fix
    turns on `--git-common-dir` resolving to the primary checkout's `.git`, which
    only a genuine worktree arranges.
    """
    _commit_all(repo)
    wt = repo / ".worktrees" / name
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", name, str(wt)],
        cwd=repo,
        check=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin"},
    )
    assert not (wt / ".env").exists(), "a worktree that starts with a .env proves nothing"
    return wt


def test_worktree_falls_back_to_the_main_checkouts_env(tmp_path):
    """#296: no `.env` here, so read the primary checkout's — the gate must run."""
    repo = _scratch_repo(tmp_path)
    wt = _worktree_of(repo, "wt")
    log = tmp_path / "stub.log"

    result = _run(wt, env_extra={"STUB_LOG": str(log)})

    assert result.returncode == 0, result.stderr
    assert "TEST_DATABASE_URL=postgresql://stub/testdb" in log.read_text()


def test_the_fallback_announces_itself(tmp_path):
    """Silence would make the gate's environment depend on an invisible probe.

    `2>/dev/null` swallowing the missing file is how #296 stayed invisible for
    the ~60s it took the test phase to die; a fallback that also says nothing
    would trade one silent behaviour for another.
    """
    repo = _scratch_repo(tmp_path)
    wt = _worktree_of(repo, "wt")

    result = _run(wt, env_extra={"STUB_LOG": str(tmp_path / "stub.log")})

    assert str(repo / ".env") in result.stderr


def test_a_worktrees_own_env_wins_over_the_fallback(tmp_path):
    """The fallback is a fallback. An operator who seeds a worktree .env means it."""
    repo = _scratch_repo(tmp_path)
    wt = _worktree_of(repo, "wt")
    (wt / ".env").write_text("TEST_DATABASE_URL=postgresql://stub/worktree-db\n")
    log = tmp_path / "stub.log"

    result = _run(wt, env_extra={"STUB_LOG": str(log)})

    assert result.returncode == 0, result.stderr
    assert "TEST_DATABASE_URL=postgresql://stub/worktree-db" in log.read_text()


def test_no_env_anywhere_is_an_actionable_error(tmp_path):
    """The remediation must name a path that exists in the failing checkout.

    Without this the gate `exec`s with a half-loaded environment and dies ~60s
    later inside pytest, printing advice that expands to a file which does not
    carry the variable. Refusing here costs a minute of nothing and says which
    files were actually consulted.
    """
    repo = _scratch_repo(tmp_path)
    (repo / ".env").unlink()
    log = tmp_path / "stub.log"

    result = _run(repo, env_extra={"STUB_LOG": str(log)})

    assert result.returncode == 2
    assert "TEST_DATABASE_URL" in result.stderr
    assert str(repo / ".env") in result.stderr
    assert not log.exists(), "the delegate ran anyway; the gate should not have started"

    # One line per file consulted. Without the fallback the repo-root path is
    # both "the .env here" and "the .env we settled on", and printing it twice
    # makes the reader doubt a list whose only job is precision.
    consulted = [
        line
        for line in result.stderr.splitlines()
        if str(repo / ".env") in line and ("(absent)" in line or "(read;" in line)
    ]
    assert len(consulted) == 1, f"path listed more than once: {consulted}"


def test_the_system_env_file_is_loaded(tmp_path):
    """Both files, in order — the repo-root one overrides, as AGENTS.md documents."""
    repo = _scratch_repo(tmp_path)
    system_env = tmp_path / "system.env"
    system_env.write_text("SYSTEM_ONLY=from-system\nTEST_DATABASE_URL=postgresql://sys/db\n")
    log = tmp_path / "stub.log"

    result = _run(repo, env_extra={"STUB_LOG": str(log), "PRE_SHIP_SYSTEM_ENV": str(system_env)})

    assert result.returncode == 0, result.stderr
    recorded = log.read_text()
    assert "SYSTEM_ONLY=from-system" in recorded
    assert "TEST_DATABASE_URL=postgresql://stub/testdb" in recorded


def test_the_default_system_env_path_is_the_documented_one():
    """`PRE_SHIP_SYSTEM_ENV` exists for tests and other hosts; prod must not need it."""
    assert "PRE_SHIP_SYSTEM_ENV:-/etc/usa-wa/.env" in SCRIPT.read_text()
