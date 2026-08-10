"""The one-shot PM subscription bootstrap's CLI surface.

Its handler is exercised through :mod:`usa_wa_sync_powermap.registry`'s reconciler tests;
what is pinned here is the flag surface, because the #179b sweep is what put a parser on
this entry point at all — it had none before.

Named ``test_pm_bootstrap`` rather than mirroring ``bootstrap.py`` exactly: the WSL adapter
already owns ``test_bootstrap.py``, and with no ``__init__.py`` in the test trees pytest
resolves the two basenames to one module and refuses to collect the second.
"""

import pytest

from clearinghouse_core.job import EXIT_CONFIG
from usa_wa_sync_powermap import bootstrap as cli


def test_the_bootstrap_declines_the_dry_run_flag():
    """It POSTs subscriptions to PM and commits its own local session, so it can honour
    neither half of the harness's "run the work but roll back" (CR #196 finding 47).

    Accepting the flag and ignoring it would have been the worst option: an operator
    reaching for ``--dry-run`` before a cutover would have registered the whole WA subtree
    on PM and been told ``dry_run=true``. Idempotence is the safety property here — a
    second run finds everything subscribed and does nothing — not a rollback.
    """
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--dry-run"])
    assert excinfo.value.code == EXIT_CONFIG
