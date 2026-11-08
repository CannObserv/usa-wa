"""This repo is a client of the cohort's shared SocratiCode store (#556).

The store is ``co-index`` (CannObserv/notifier#57; procedures in CannObserv/index).
Three tracked pieces make this checkout a client, and one host-local piece
authenticates it:

* ``.socraticode.json`` names the project ``usa-wa``, so its collections read as
  ``codebase_usa-wa`` in the shared store rather than a hash of the checkout path;
* ``.claude/settings.json`` ``env`` points the server at the external Qdrant and
  Ollama — anything else falls back to ``managed`` mode and local containers;
* two namespace guards: neither ``QDRANT_COLLECTION_PREFIX`` nor
  ``SOCRATICODE_BRANCH_AWARE`` may reach the server from anywhere on this host;
* the key file, below.

``.claude/settings.local.json`` holds this host's ``QDRANT_API_KEY`` for the
cohort's shared ``co-index`` store. Qdrant has one global key, so a leak from
any repo means rotating it on every cohort host at once, with no overlap
window — and this repo is public. CannObserv/index's ``install_qdrant_key.sh``
refuses a target where ``git check-ignore`` prints nothing; these tests keep
that precondition from regressing.

Both probes ask git rather than reading ``.gitignore``: a rule can be shadowed
by a later negation, and a tracked file is committed whatever the rules say.
"""

import json
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SETTINGS = REPO / ".claude" / "settings.json"
CONFIG = REPO / ".socraticode.json"
SOCRATICODE_DOC = REPO / "docs" / "SOCRATICODE.md"

#: The client block, verbatim. CannObserv/archiver#226 § Traps 3-6: QDRANT_URL, never
#: QDRANT_HOST; the full MagicDNS name, because the key needs TLS; Ollama over plain
#: HTTP on the tailnet short name.
CLIENT_ENV = {
    "QDRANT_MODE": "external",
    "QDRANT_URL": "https://index.taild0fb76.ts.net:6333",
    "OLLAMA_MODE": "external",
    "OLLAMA_URL": "http://index:11434",
    "EMBEDDING_MODEL": "nomic-embed-text",
    "EMBEDDING_DIMENSIONS": "768",
}

#: Upstream's own validator (config.js assertValidProjectId) rejects anything else.
PROJECT_ID_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")

#: Set anywhere, and the shared namespace splits while every health check stays green:
#: the prefix renames the cohort-global metadata collection too, and branch-awareness
#: mints a fresh, empty collection set per branch.
NAMESPACE_GUARDS = ("QDRANT_COLLECTION_PREFIX", "SOCRATICODE_BRANCH_AWARE")


def _primary_checkout() -> Path:
    """The checkout the key is installed into; a worktree is not it.

    Falls back to this tree when git cannot answer, so collection never breaks.
    """
    out = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--path-format=absolute", "--git-common-dir"],
        capture_output=True,
        text=True,
        check=False,
    )
    return Path(out.stdout.strip()).parent if out.returncode == 0 else REPO


PRIMARY = _primary_checkout()
HOME = Path.home()

#: Every channel that reaches the MCP server's environment here, by a stable label: the
#: project settings (this tree's and the primary checkout's local file — in the primary
#: checkout the two are one), the user's, and the VS Code machine setting that launches
#: ``claude`` (where ``SOCRATICODE_SPEC`` really takes effect). Host-local ones skip when
#: absent. ``env`` keys are read where the file has them, so a comment naming a variable
#: is not a finding; the VS Code file is matched as text.
NAMESPACE_CHANNELS = {
    "project settings.json": (SETTINGS, "env"),
    "this tree's settings.local.json": (REPO / ".claude" / "settings.local.json", "env"),
    "primary settings.local.json": (PRIMARY / ".claude" / "settings.local.json", "env"),
    "user ~/.claude/settings.json": (HOME / ".claude" / "settings.json", "env"),
    "VS Code machine settings": (
        HOME / ".vscode-server" / "data" / "Machine" / "settings.json",
        None,
    ),
}

#: The retired local store's tooling (#394), gone with the image it slimmed.
RETIRED = ("slim-ollama" + "-image", "dev.usa-wa" + ".slim")

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


def _env() -> dict:
    return json.loads(SETTINGS.read_text()).get("env", {})


def test_project_id_is_the_repo_name() -> None:
    """``usa-wa``, the cohort convention: collections read as ``codebase_usa-wa``.

    A malformed file is silently ignored upstream and degrades to the path hash, so
    parse it here.
    """
    assert json.loads(CONFIG.read_text())["projectId"] == "usa-wa"


def test_project_id_is_qdrant_safe() -> None:
    """Upstream throws on anything outside ``[a-zA-Z0-9_-]`` rather than sanitising."""
    assert set(json.loads(CONFIG.read_text())["projectId"]) <= PROJECT_ID_CHARS


@pytest.mark.parametrize(("name", "value"), sorted(CLIENT_ENV.items()))
def test_settings_point_at_co_index(name: str, value: str) -> None:
    """A missing or wrong variable falls back to ``managed`` mode and local containers."""
    assert _env().get(name) == value, f"settings.json env {name} is not {value!r}"


def test_settings_use_qdrant_url_not_host() -> None:
    """``QDRANT_HOST`` builds a plain-HTTP URL, and the key is refused without TLS."""
    assert "QDRANT_HOST" not in _env()


def test_the_key_is_not_in_tracked_settings() -> None:
    """The key belongs in the ignored settings.local.json; settings.json is public."""
    assert "QDRANT_API_KEY" not in _env()


def _names_set(path: Path, key: str | None) -> list[str]:
    """The guarded names a channel sets. Never returns, logs or asserts on a value."""
    text = path.read_text()
    if key is None:
        return [name for name in NAMESPACE_GUARDS if name in text]
    env = json.loads(text).get(key) or {}
    return [name for name in NAMESPACE_GUARDS if name in env]


@pytest.mark.parametrize("channel", sorted(NAMESPACE_CHANNELS))
def test_no_namespace_splitting_variable(channel: str) -> None:
    """VM-local where the channel is VM-local; skips loudly rather than passing vacuously."""
    path, key = NAMESPACE_CHANNELS[channel]
    if not path.is_file():
        pytest.skip(f"{channel}: {path} absent on this host")
    found = _names_set(path, key)
    assert not found, f"{channel} ({path}) sets {found}: the shared store's namespace splits"


@pytest.mark.parametrize(
    "phrase",
    [
        "127.0.0.1:16333:6333",
        "127.0.0.1:16334:6334",
        "127.0.0.1:11435:11434",
        "QDRANT__TELEMETRY_DISABLED=true",
        # The knob records Docker as purged; a reinstall that leaves the line hides
        # dockerd and the docker group from every patching run until it expires.
        "exception purged:docker",
    ],
)
def test_the_way_back_stays_on_loopback(phrase: str) -> None:
    """Re-enabling a local store must publish on loopback, telemetry off (skills#494).

    SocratiCode's own ``docker run -p <port>:…`` binds every interface, and Qdrant
    there asks for no key, so the documented way back pre-creates the containers.
    """
    assert phrase in SOCRATICODE_DOC.read_text(), f"docs/SOCRATICODE.md lacks {phrase!r}"


@pytest.mark.parametrize("needle", RETIRED)
def test_the_local_store_tooling_is_retired(needle: str) -> None:
    """No tracked file still runs or documents the slim-ollama rebuild."""
    grep = _git("grep", "-l", "-F", needle)
    assert grep.returncode in (0, 1), f"git grep failed, searched nothing: {grep.stderr}"
    hits = grep.stdout.split()
    assert not hits, f"{needle!r} still named in {hits}"
