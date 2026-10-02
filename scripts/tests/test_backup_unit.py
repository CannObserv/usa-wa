"""The nightly backup's unit is a sandbox, and every line of it is load-bearing (#434).

``usa-wa-backup.service`` runs repo code nightly and unattended, holding the one key
that can write the backup bucket. What that code can reach is the whole question,
so the shape follows CannObserv/watcher#297: its own dynamic user with no
capabilities, a read-only database role reached by peer auth, the key handed over
as a systemd credential, and a ``/home`` holding nothing but the checkout — minus
the checkout's ``.env``, which is world-readable here (0644) and carries the agent
tokens. docs/RECOVERY.md explains each line; this pins them.

Pure file parse — no DB, no systemd — except one host-dependent check: the venv's
interpreter must resolve outside the hidden ``/home`` where a venv exists to look at.
"""

from __future__ import annotations

import re
import shlex
from pathlib import Path

from systemd_units import DEPLOY, unit_value, unit_values

from usa_wa_api.backup.checkin import KEY_CREDENTIAL

SERVICE = DEPLOY / "usa-wa-backup.service"
TIMER = DEPLOY / "usa-wa-backup.timer"
PIPELINE_TIMER = DEPLOY / "usa-wa-pipeline.timer"
ROLE_SQL = DEPLOY.parent / "scripts" / "setup-backup-role.sql"
NIGHTLY_SCRIPT = DEPLOY.parent / "scripts" / "pipeline-nightly.sh"

CHECKOUT = "/home/exedev/usa-wa"
BACKUP_MODULE = "usa_wa_api.backup.run"
NIGHTLY_ROOT_RE = re.compile(r'^cd "\$\{PIPELINE_NIGHTLY_ROOT:-(?P<root>[^}]+)\}"', re.MULTILINE)
DAILY_RE = re.compile(r"^\*-\*-\* (\d{2}):(\d{2}):\d{2} UTC$")


def _service(key: str) -> list[str]:
    return unit_values(SERVICE, "Service", key)


def _environment() -> dict[str, str]:
    """Every ``Environment=`` assignment, split the way systemd splits them."""
    pairs = (
        token.partition("=") for line in _service("Environment") for token in shlex.split(line)
    )
    return {name: value for name, _, value in pairs}


def test_its_own_user_and_no_privilege() -> None:
    assert unit_value(SERVICE, "Service", "DynamicUser") == "yes"
    assert unit_value(SERVICE, "Service", "User") == "usa_wa_backup"
    assert _service("CapabilityBoundingSet") == [""]
    assert unit_value(SERVICE, "Service", "NoNewPrivileges") == "yes"
    assert unit_value(SERVICE, "Service", "ProtectSystem") == "strict"
    assert unit_value(SERVICE, "Service", "PrivateTmp") == "yes"
    assert unit_value(SERVICE, "Service", "RestrictAddressFamilies") == "AF_UNIX AF_INET AF_INET6"


def test_the_database_role_is_the_units_user() -> None:
    """Peer auth maps the OS user to the role of the same name."""
    assert "\\set backup_role usa_wa_backup" in ROLE_SQL.read_text()


def test_a_home_holding_only_the_checkout_and_not_its_secrets() -> None:
    assert unit_value(SERVICE, "Service", "ProtectHome") == "tmpfs"
    assert _service("BindReadOnlyPaths") == [CHECKOUT]
    hidden = {path.lstrip("-") for line in _service("InaccessiblePaths") for path in line.split()}
    assert hidden == {f"{CHECKOUT}/.env", f"{CHECKOUT}/.worktrees"}


def test_the_keys_are_credentials_never_the_environment() -> None:
    """The GCS key and the dead-man check-in key (#455). systemd 255 fails the start
    (243/CREDENTIALS) on a missing source, so the check-in key's file must exist —
    empty until the monitor does — and the job reads empty as unconfigured."""
    assert _service("LoadCredential") == [
        "gcs:/etc/usa-wa/co-usa-wa-backup.json",
        f"{KEY_CREDENTIAL}:/etc/usa-wa/backup-checkin.key",
    ]
    assert _environment()["GOOGLE_APPLICATION_CREDENTIALS"] == "%d/gcs"
    assert not any("CHECKIN_KEY" in name for name in _environment())


def test_its_configuration_and_nothing_else() -> None:
    """Never /etc/usa-wa/.env: the database URLs are in it, and this unit needs none.
    No leading '-': a backup unit without its configuration must fail loudly."""
    assert _service("EnvironmentFile") == ["/etc/usa-wa/backup.env"]


def test_both_guards_run_inside_the_sandbox() -> None:
    """The guards run as the dynamic user, which does not own the checkout — git's
    ownership check refuses it unless the checkout is named safe."""
    pre = [value.split()[0] for value in _service("ExecStartPre")]
    assert pre == [
        f"{CHECKOUT}/scripts/assert-main-checkout.sh",
        f"{CHECKOUT}/scripts/assert-venv-integrity.sh",
    ]
    env = _environment()
    assert env["GIT_CONFIG_COUNT"] == "1"
    assert env["GIT_CONFIG_KEY_0"] == "safe.directory"
    assert env["GIT_CONFIG_VALUE_0"] == CHECKOUT


def test_it_mirrors_the_store_the_harvests_write() -> None:
    match = NIGHTLY_ROOT_RE.search(NIGHTLY_SCRIPT.read_text())
    assert match, f"{NIGHTLY_SCRIPT.name} no longer changes into a default root"
    assert _environment()["USA_WA_RAW_ROOT"] == f"{match['root']}/raw"


def test_it_runs_the_venv_interpreter_not_uv() -> None:
    """uv wants a writable cache and may sync the environment, both refused here."""
    (command,) = _service("ExecStart")
    tokens = shlex.split(command)
    assert tokens[0] == f"{CHECKOUT}/.venv/bin/python"
    assert tokens[1:3] == ["-m", BACKUP_MODULE]
    assert "--dry-run" not in tokens


def test_the_interpreter_is_not_hidden_by_the_empty_home() -> None:
    """A uv-managed python under ~/.local/share/uv would vanish under ProtectHome=tmpfs
    and the unit would fail 203/EXEC. Checked against this checkout's venv, if any."""
    python = Path(__file__).parents[2] / ".venv" / "bin" / "python"
    if not python.exists():
        return
    resolved = python.resolve()
    assert not resolved.is_relative_to("/home") or resolved.is_relative_to(CHECKOUT), resolved


def test_failure_alerts() -> None:
    assert unit_values(SERVICE, "Unit", "OnFailure") == ["usa-wa-notify-failure@%n.service"]


def test_the_timer_fires_daily_after_the_pipeline_and_catches_up() -> None:
    """The registrar and operator edits are what change; ship them the same day."""
    (backup,) = unit_values(TIMER, "Timer", "OnCalendar")
    (pipeline,) = unit_values(PIPELINE_TIMER, "Timer", "OnCalendar")
    backup_at, pipeline_at = DAILY_RE.match(backup), DAILY_RE.match(pipeline)
    assert backup_at and pipeline_at, (backup, pipeline)
    assert int(backup_at[1]) >= int(pipeline_at[1]) + 2, "too close behind the pipeline"
    assert unit_value(TIMER, "Timer", "Persistent") == "true"


def test_ordered_after_the_tailnet_but_never_bound_to_it() -> None:
    """The check-in reaches co-status over the tailnet (#455). A boot-time catch-up run
    must not race MagicDNS, but a Tailscale restart must never touch the backup — and
    a host off the tailnet still ships its backup, which the monitor then reports."""
    after = " ".join(unit_values(SERVICE, "Unit", "After")).split()
    assert "tailscaled.service" in after
    for key in ("Wants", "Requires", "BindsTo", "PartOf"):
        bound = " ".join(unit_values(SERVICE, "Unit", key)).split()
        assert "tailscaled.service" not in bound, key
