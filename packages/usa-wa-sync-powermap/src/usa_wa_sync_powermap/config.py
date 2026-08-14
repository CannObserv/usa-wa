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
    #: Central courtesy floor (seconds) between any two PM HTTP calls (#85, the
    #: #77 pattern): an httpx request hook inside GeneratedPowerMapClient, so no
    #: caller — the anchored-cohort backstop's ~300-GET crawl, a harvest CLI — can
    #: burst PM's live rate limit (429 since the #84 companion hardening; the
    #: observed trip was ~8 req/s). Default 0.2 (≤5 req/s); 0 disables.
    powermap_min_request_interval: float = 0.2
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
    #: Changes-feed replay backstop (usa-wa#159, re-costed in usa-wa#211). ``replay_enabled``
    #: is the kill switch for the trailing re-read that re-covers PM's at-least-once
    #: concurrent-commit skip (power-map#387). Each pass floors at the persisted verified
    #: watermark minus ``replay_retain`` — a small trail sized against PM's worst-case
    #: in-flight-write span (see :data:`~clearinghouse_sync_powermap.engine
    #: .DEFAULT_REPLAY_RETAIN`) — so the window narrows as the stream converges;
    #: ``replay_margin`` is only the one-off bootstrap depth for a stream with no
    #: watermark. ``replay_max_items`` bounds one pass's enumeration (≤ one detail GET
    #: per item — a 304 still spends a rate-limit token) with lossless carry-over via
    #: the watermark; ``replay_cadence`` runs from pass *end* (the sidecar end-stamps),
    #: so a full cadence of idle PM time separates passes whatever a pass costs.
    #: Steady-state cost is the retained trail plus feed novelty — a few hundred detail
    #: GETs per pass, minutes of paced traffic per hour. Before #211 the floor was
    #: pinned at ``high_water − margin`` and the same ~10k-seq window re-read every
    #: pass forever: 117,932 requests in 19h at 1.73–1.97 req/s against the 2 req/s
    #: ceiling, ending in a PM 503 — the docstring's old "hourly + cheap" claim was
    #: measured false, which is what #211 fixed.
    replay_enabled: bool = True
    replay_margin: int = 10_000
    replay_cadence: timedelta = timedelta(hours=1)
    replay_retain: int = 1_000
    replay_max_items: int = 2_000
    #: Conditional GET on the two backstop read paths — the anchored-cohort reconcile and
    #: the trailing changes-feed replay (usa-wa#160): send PM's stored ETag as If-None-Match
    #: and skip a full re-fetch + re-apply on a 304 (power-map#385). The *live* feed stays
    #: unconditional (a feed item means PM changed the entity → a guaranteed 200).
    #: Kill switch (default true) so a suspected PM ETag bug can be disabled without a
    #: redeploy — off = the unchanged unconditional full-fetch every pass.
    conditional_get_enabled: bool = True
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
    #: Non-convergence backstop (usa-wa#112): consecutive identical ``auto-attached``
    #: re-sends of an already-anchored row before it is flagged as non-converging (PM
    #: keeps matching without applying our diff). Re-enqueue is reconcile-gated (12h), so
    #: 3 ≈ ~1.5 days of proven-futile churn before an operator-visible flag + rise email.
    nonconvergence_threshold: int = 3
    #: Recipient for the failure-streak alert (#85) — the same ``USA_WA_ALERT_EMAIL``
    #: the #49 oneshot handler reads (`/etc/usa-wa/.env`). Unset = alerting disabled;
    #: the daemon warns loudly at startup (fail-closed, like notify-failure.sh).
    usa_wa_alert_email: str | None = None


@lru_cache
def get_sidecar_settings() -> SidecarSettings:
    return SidecarSettings()
