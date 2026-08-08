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

    if with_delegate:
        delegate = tmp_path / DELEGATE_REL
        delegate.parent.mkdir(parents=True)
        delegate.write_text(STUB)
        delegate.chmod(0o755)

    return tmp_path


def _run(repo: Path, *args: str, env_extra: dict | None = None):
    # Deliberately *not* inheriting os.environ: the clean-shell case is the bug.
    # PATH alone keeps git/bash resolvable.
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin"}
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
