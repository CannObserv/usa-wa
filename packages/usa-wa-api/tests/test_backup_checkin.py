"""The nightly backup's dead-man check-in (#455).

A backup that fails loudly still says nothing when it stops running — a disabled
timer, a unit never installed, a wedged interpreter all produce zero failures and
zero traffic. So the job reports every run, success or not, to a co-status monitor
that alarms when a report fails to arrive. Ported from CannObserv/watcher's
``src/ops/checkin.py``, whose contract co-status kept from notifier.

The key is a systemd credential, never an environment variable: the unit's
``LoadCredential=`` hands the run a private copy under ``$CREDENTIALS_DIRECTORY``.
Here a ``tmp_path`` directory plays that part.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx
import pytest

from usa_wa_api.backup import checkin

MONITOR = "01M24A8CA2GT0M7WE57NEMD0EW"
#: RFC 2606's reserved TLD: never resolves, so never a real connection target.
BASE = "http://status.invalid:9000"
KEY = "sk_backup"
LOGGER = "usa_wa_api.backup.checkin"


def _credentials(directory: Path, key: str | None = KEY) -> dict[str, str]:
    """A credentials directory holding ``key`` (no file at all when None)."""
    directory.mkdir(exist_ok=True)
    if key is not None:
        (directory / checkin.KEY_CREDENTIAL).write_text(key)
    return {checkin.CREDENTIALS_DIRECTORY_ENV: str(directory)}


@pytest.fixture
def configured(tmp_path) -> dict[str, str]:
    """All three: the base and monitor id from the environment, the key from its
    credential."""
    return {
        checkin.BASE_URL_ENV: BASE,
        checkin.MONITOR_ID_ENV: MONITOR,
        **_credentials(tmp_path / "credentials"),
    }


class Recorder:
    """A POST seam that records calls and answers from a script."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, dict, dict]] = []

    def __call__(self, url: str, payload: dict, headers: dict, timeout: float) -> int:
        self.calls.append((url, payload, headers))
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _events(caplog, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


class TestPostCheckin:
    def test_posts_to_the_configured_monitor_with_the_key(self, configured) -> None:
        post = Recorder(202)
        assert checkin.post_checkin("ok", {"outcome": "ok"}, environ=configured, post=post)
        ((url, payload, headers),) = post.calls
        assert url == f"{BASE}/api/v1/monitors/{MONITOR}/checkin"
        assert payload == {"status": "ok", "variables": {"outcome": "ok"}}
        assert headers["X-API-Key"] == KEY

    def test_a_trailing_slash_on_the_base_is_not_doubled(self, configured) -> None:
        post = Recorder(202)
        checkin.post_checkin(
            "ok", {}, environ={**configured, checkin.BASE_URL_ENV: BASE + "/"}, post=post
        )
        assert post.calls[0][0] == f"{BASE}/api/v1/monitors/{MONITOR}/checkin"

    @pytest.mark.parametrize(
        "base",
        ["status.invalid:9000", "http://[status.invalid:9000", "http://status.invalid:9o00"],
    )
    def test_a_base_that_is_not_an_http_url_is_refused_not_raised(
        self, configured, caplog, base
    ) -> None:
        """A typo in ``backup.env`` must read as one, not as a traceback: ``urlsplit``
        raises on a bad bracket and ``.port`` on a bad port."""
        post = Recorder()
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            assert not checkin.post_checkin(
                "ok", {}, environ={**configured, checkin.BASE_URL_ENV: base}, post=post
            )
        assert post.calls == []
        assert _events(caplog, logging.ERROR) == ["backup_checkin_config_error"]

    def test_unconfigured_warns_and_posts_nothing(self, caplog) -> None:
        post = Recorder()
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ={}, post=post)
        assert post.calls == []
        assert _events(caplog, logging.WARNING) == ["backup_checkin_unconfigured"]

    def test_unconfigured_under_the_unit_warns_and_posts_nothing(self, tmp_path, caplog) -> None:
        """What the unit hands the job until the monitor exists: its key file must
        exist, so it is empty, and neither variable is set — a warning, not an error."""
        post = Recorder()
        environ = _credentials(tmp_path / "credentials", key="")
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ=environ, post=post)
        assert post.calls == []
        assert [r.levelno for r in caplog.records] == [logging.WARNING]

    @pytest.mark.parametrize("missing", [checkin.BASE_URL_ENV, checkin.MONITOR_ID_ENV])
    def test_half_configured_is_an_error_and_posts_nothing(
        self, configured, caplog, missing
    ) -> None:
        """All three or none: half a configuration must say so rather than look wired."""
        post = Recorder()
        environ = {name: value for name, value in configured.items() if name != missing}
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ=environ, post=post)
        assert post.calls == []
        [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert record.getMessage() == "backup_checkin_config_error"
        assert missing in record.error

    @pytest.mark.parametrize("key", ["", None], ids=["empty", "absent"])
    def test_a_key_credential_empty_or_absent_is_half_configured(
        self, configured, tmp_path, caplog, key
    ) -> None:
        post = Recorder()
        environ = {**configured, **_credentials(tmp_path / "other", key=key)}
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ=environ, post=post)
        assert post.calls == []
        [record] = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert checkin.KEY_CREDENTIAL in record.error

    def test_a_monitor_id_that_is_not_a_bare_id_is_refused(self, configured, caplog) -> None:
        """It becomes a URL path segment."""
        post = Recorder()
        environ = {**configured, checkin.MONITOR_ID_ENV: "../tenants/x"}
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ=environ, post=post)
        assert post.calls == []

    def test_a_server_error_is_retried_once(self, configured) -> None:
        """Retry-safe by contract: a replayed check-in overwrites the first."""
        post = Recorder(503, 202)
        assert checkin.post_checkin("alert", {}, environ=configured, post=post)
        assert len(post.calls) == 2

    def test_a_transport_error_is_retried_once(self, configured) -> None:
        post = Recorder(httpx.ConnectError("no route"), 202)
        assert checkin.post_checkin("ok", {}, environ=configured, post=post)
        assert len(post.calls) == 2

    def test_a_rejection_is_not_retried(self, configured, caplog) -> None:
        """A 404 is the wrong id (a tenant id is the same ULID shape), a 401 the wrong
        key: repeating either changes nothing."""
        post = Recorder(404)
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ=configured, post=post)
        assert len(post.calls) == 1
        [record] = caplog.records
        assert record.getMessage() == "backup_checkin_rejected"
        assert record.http_status == 404

    def test_persistent_failure_gives_up_quietly(self, configured, caplog) -> None:
        post = Recorder(httpx.ConnectError("down"), httpx.ConnectError("down"))
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ=configured, post=post)
        assert len(post.calls) == 2
        assert _events(caplog, logging.WARNING) == ["backup_checkin_failed"]

    @pytest.mark.parametrize(
        "error",
        [
            UnicodeEncodeError("ascii", "sk_bäckup", 4, 5, "ordinal not in range(128)"),
            httpx.InvalidURL("bad"),
            RuntimeError("anything else"),
        ],
    )
    def test_an_error_that_is_not_transport_is_contained(self, configured, caplog, error) -> None:
        """Nothing may escape: a check-in that raised would fail the backup it reports
        on. Logged by type, never by message, which may quote the key."""
        post = Recorder(error)
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ=configured, post=post)
        assert len(post.calls) == 1
        [record] = caplog.records
        assert record.error == type(error).__name__
        assert "sk_b" not in record.error


class TestKeyCredential:
    """The key is read from ``$CREDENTIALS_DIRECTORY`` and nowhere else."""

    def test_the_key_is_never_taken_from_the_environment(self, caplog) -> None:
        """A key in the environment is in every child's and in ``/proc/<pid>/environ``."""
        post = Recorder(202)
        environ = {
            checkin.BASE_URL_ENV: BASE,
            checkin.MONITOR_ID_ENV: MONITOR,
            "USA_WA_BACKUP_CHECKIN_KEY": KEY,
        }
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ=environ, post=post)
        assert post.calls == []

    def test_the_trailing_newline_of_a_key_file_is_not_part_of_the_key(
        self, configured, tmp_path
    ) -> None:
        post = Recorder(202)
        environ = {**configured, **_credentials(tmp_path / "nl", key=f"{KEY}\n")}
        assert checkin.post_checkin("ok", {}, environ=environ, post=post)
        assert post.calls[0][2]["X-API-Key"] == KEY

    def test_a_key_that_cannot_be_read_is_contained(self, configured, tmp_path, caplog) -> None:
        directory = tmp_path / "odd"
        (directory / checkin.KEY_CREDENTIAL).mkdir(parents=True)
        post = Recorder(202)
        environ = {**configured, checkin.CREDENTIALS_DIRECTORY_ENV: str(directory)}
        with caplog.at_level(logging.ERROR, logger=LOGGER):
            assert not checkin.post_checkin("ok", {}, environ=environ, post=post)
        assert post.calls == []
        [record] = caplog.records
        assert record.error == "IsADirectoryError"


class TestNames:
    """The host's contract — ``backup.env`` and the unit's ``LoadCredential=`` spell
    them — naming no service, so a move of the monitor is configuration alone."""

    def test_the_variables(self) -> None:
        assert checkin.BASE_URL_ENV == "USA_WA_BACKUP_CHECKIN_BASE_URL"
        assert checkin.MONITOR_ID_ENV == "USA_WA_BACKUP_MONITOR_ID"

    def test_the_key_credential(self) -> None:
        assert checkin.KEY_CREDENTIAL == "checkin-key"
