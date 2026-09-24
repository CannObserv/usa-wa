"""Roster ↔ WSL sponsor exact rule (#308) — thin binder over the pure,
pytest-covered `usa_wa_pipeline.matching.roster_wsl.roster_wsl_links`
(docs/PIPELINE.md § TDD policy: logic lives in importable functions)."""

from usa_wa_pipeline.frames import typed_relation
from usa_wa_pipeline.matching.roster_wsl import LINK_SCHEMA, roster_wsl_links


def model(dbt, session):
    dbt.config(materialized="table")
    links = roster_wsl_links(dbt.ref("stg_roster_members").df(), dbt.ref("stg_wsl_sponsors").df())
    return typed_relation(session, links, LINK_SCHEMA)
