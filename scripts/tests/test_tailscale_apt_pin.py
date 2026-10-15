"""Tailscale's apt origin is followed, and scoped to what it was added for (#497).

The patching-hosts knob says ``origin pkgs.tailscale.com follow``, so the
monthly maintenance lane takes the site's upgrades. At apt's default priority,
500, the site ties Ubuntu's archive: a package it served under an Ubuntu name at
a higher version would replace Ubuntu's (the skill's policy.md, "Two lanes";
NodeSource replaced Ubuntu's ``nodejs`` that way on CannObserv/processor#344).
So the whole site sits below 500 and the two packages installed from it above,
and the probe stops reporting ``unscoped:``/``unpinned:``. Same file as
CannObserv/replicator#131.

These tests read the deploy artifact, not the live host: what the host carries
is verified at install time — ``docs/DEPLOYMENT-HOST.md`` § OS updates.
"""

from __future__ import annotations

from systemd_units import DEPLOY

PIN = DEPLOY / "apt-preferences.d" / "tailscale.pref"
SITE = "pkgs.tailscale.com"
# What apt installed from the site: the client, and the keyring it pulled in.
INSTALLED_FROM_SITE = {"tailscale", "tailscale-archive-keyring"}


def _stanzas() -> list[dict[str, str]]:
    """The file's stanzas as apt reads them: blank-line separated, ``#`` comments dropped."""
    stanzas, current = [], {}
    for line in PIN.read_text().splitlines() + [""]:
        if line.startswith("#"):
            continue
        if not line.strip():
            if current:
                stanzas.append(current)
            current = {}
            continue
        key, value = line.split(":", 1)
        current[key.strip()] = value.strip()
    return stanzas


def test_apt_reads_the_file() -> None:
    """apt ignores a preferences.d file whose extension is neither ``.pref`` nor none."""
    assert PIN.suffix == ".pref"


def test_every_stanza_pins_the_site() -> None:
    stanzas = _stanzas()
    assert stanzas
    for stanza in stanzas:
        assert set(stanza) == {"Package", "Pin", "Pin-Priority"}, stanza
        assert stanza["Pin"] == f"origin {SITE}"


def test_the_site_sits_below_ubuntu() -> None:
    (catch_all,) = [s for s in _stanzas() if s["Package"] == "*"]
    assert int(catch_all["Pin-Priority"]) < 500


def test_what_was_installed_from_it_sits_above() -> None:
    named = {
        package: int(s["Pin-Priority"])
        for s in _stanzas()
        if s["Package"] != "*"
        for package in s["Package"].split()
    }
    assert set(named) == INSTALLED_FROM_SITE
    assert all(priority > 500 for priority in named.values()), named
