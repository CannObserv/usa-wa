"""Sidecar settings via pydantic-settings.

Env (`/etc/usa-wa/.env`, repo `.env`) is loaded by systemd or the developer
before launch — never by this module. PM credentials live there.
"""

from datetime import timedelta
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class SidecarSettings(BaseSettings):
    """Power Map sidecar runtime configuration."""

    model_config = SettingsConfigDict(extra="ignore")

    powermap_base_url: str = "https://power-map.exe.xyz"
    powermap_api_key: str | None = None
    #: Seconds between sync cycles (feed poll + due reconcile + outbox drain).
    feed_poll_seconds: float = 60.0
    #: Outbox delivery transaction boundary (#8): how many delivered entries to
    #: batch per DB commit during a drain. Default 1 = commit per entry, so a slow
    #: PM never holds one open transaction across N network round-trips; raise it
    #: to amortise commit cost when throughput dominates over lock-hold latency.
    outbox_commit_chunk_size: int = 1
    #: Subscription discovery (PM #203): where the WA-subtree traversal starts and
    #: which edges it follows. The default mirrors the design's usa-wa setup.
    powermap_discovery_root_type: str = "jurisdiction"
    powermap_discovery_root_id: str = "usa-wa"
    #: #73 Axis 1: PM discovery follows only the jurisdiction lineage — the mirror-only,
    #: PM-authoritative cache usa-wa does not produce. The producer subtree edges
    #: (affiliated_orgs/org_children/roles/assignments/people) are dropped: those rows
    #: are subscribed from OUR local anchored cohort (``include_local_cohort``) instead,
    #: so discovery stops dragging in ~1,000 PM-only strangers we never mirror.
    powermap_discovery_follow: list[str] = ["lineage"]
    #: How often the in-loop re-discovery backstop re-runs (catches graph drift —
    #: e.g. a newly-added WA committee). Bootstrap covers the initial population.
    #: Six-hourly (#73 Axis 2): graph drift is slow — new committees enter via the
    #: daily WSL refresh, so an hourly full-subtree re-discovery walk was wasteful.
    subscription_backstop_cadence: timedelta = timedelta(hours=6)
    #: Anchored-cohort reconcile cadence (#73 Axis 2): overrides each producer
    #: descriptor's per-entity ``reconcile_cadence`` (org/role/assignment/person) in
    #: :func:`build_descriptors`. The backstop re-fetches OUR whole anchored cohort by
    #: id (each person also pulling ``/events``) — a dropped-feed-event safety net, not
    #: the primary path — so a twice-daily sweep of a low-churn dataset is ample and
    #: cuts the steady-state ``people`` read volume the feed already covers in real time.
    reconcile_cadence: timedelta = timedelta(hours=12)
    #: PM-first match-cascade name-search cap (#12): the max candidate window the
    #: org/person descriptors page-and-confirm during a name match (passed as the
    #: search ``limit``). The exact match must rank within it, so widen this if PM's
    #: FTS ranking pushes a true match past the default window. ``None`` keeps each
    #: descriptor's historical per-entity default (orgs 50, people 20) — non-breaking.
    powermap_search_match_cap: int | None = None
    #: Failure-streak alerting (#85): after this many consecutive failed cycles the
    #: sidecar emails the operator once (re-armed by the next clean cycle). With the
    #: exponential backoff schedule (60s base, doubling, 1h cap), 5 ≈ ~30 min of
    #: continuous failure before the email.
    failure_alert_threshold: int = 5
    #: Recipient for the failure-streak alert (#85) — the same ``USA_WA_ALERT_EMAIL``
    #: the #49 oneshot handler reads (`/etc/usa-wa/.env`). Unset = alerting disabled;
    #: the daemon warns loudly at startup (fail-closed, like notify-failure.sh).
    usa_wa_alert_email: str | None = None


@lru_cache
def get_sidecar_settings() -> SidecarSettings:
    return SidecarSettings()
