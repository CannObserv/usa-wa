# Commands — succession and committee lineage

Split out of [COMMANDS.md](COMMANDS.md), which is where the index lives.

The checks that used to run beside these recorders — the Senate and House odd-year
corroborations (#123/#149), the succession invariants (#107/#119) and the committee-lineage
invariants and candidate report (#124 C4/C5) — read the canonical tier and were deleted with it in
#412 PR F. Their gates run in-build as dbt tests on the published tier
([PIPELINE.md § Ported from the canonical tier](PIPELINE.md#ported-from-the-canonical-tier-412)).

## Operator succession (#107)

Mid-biennium successions (death, resignation, appointment) are invisible to every
wire signal — the cumulative WSL wire keeps a departed member named + committee-listed,
so their tenure span stays ghost-open, and an appointee's span starts at the biennium
floor, not the appointment date. Operators know these facts (news-first) and **interject**
them as `OperatorEvent`s, stored in `registry.operator_events`. The nightly pipeline reads
them as a curated input (`usa_wa_pipeline.operator_read`) and applies each as an authoritative
**overlay** on every span family after `build_tenure_spans` (`conformed/spans.py`,
`conformed/house.py`), so the overlay re-applies every build and the wire can never win back a
corrected span (self-durable). A recorded event publishes after the next nightly. Provenance:
each write's attestation body lands in the raw store under the `usa_wa_operator` source, which
the weekly integrity sweep covers — its only provenance since #412 PR F dropped the Postgres
`FetchEvent`/`RawPayload` tables.

```bash
# Record operator succession events (#107) — the live interjection surface. Three kinds
# split by scope so a chamber move never touches the party span:
#   departed (person-scoped, no seat) — the member stops serving entirely; every open span
#     (seat + party + committee) closes at the date. Death, full resignation, expulsion.
#   vacated  (seat-scoped) — ONE named seat's span closes at the date; party + committees
#     untouched. A chamber move's old seat, or a single-seat resignation.
#   seated   (seat-scoped) — one named seat's span opens at the date (instead of the
#     biennium floor), synthesized if the wire built none. Appointment, swearing-in.
# A chamber move = vacated(old seat) + seated(new seat) on the same member, each applied to
# the span family that owns that seat kind. seat_kind/seat_discriminator name the seat the same
# way the pipeline keys it: chamber-senate + LD, chamber-house + ld-{n}-position-{p},
# committee + the WSL committee id. Validates kind/reason/seat shape AND that member_id
# is a registered person key (usa_wa_legislature:<id> in registry.entity_keys) — a typo
# would be a silent no-op overlay.
# App-role DML (writes registry.operator_events, and the attestation body — its only
# provenance since #412 PR F — to the raw store after the commit: run it from the primary
# checkout, or set USA_WA_RAW_ROOT, so it lands in the prod raw/ and not a worktree's; exit 4 =
# the write committed but the raw copy did not land — record the event again as it now
# stands, without --supersede, which a superseded prior refuses — a --file batch is re-run
# with every supersede_id removed: the write is idempotent and archives its bytes); shell access is the trust boundary. Provenance is append-only — a
# date-correction is --supersede (a NEW row stamping the prior one's superseded_by_id), never
# a mutation (#54).
# A supersede may also RECLASSIFY, within endings only (#363): departed <-> vacated are
# two readings of one boundary (left the legislature / moved seats within it), and
# append-only provenance leaves no other way to say the projection changed its mind.
# An ending can never become a beginning — that is a different fact.
# --dry-run validates + writes, then rolls back — but --list is read-only and commits even
# under --dry-run, which is why this job keeps its own transaction on the #179b harness.
# Exit 2 on a validation failure (unchanged).
python -m usa_wa_adapter_legislature.operators.cli \
    --member-id 29091 --kind departed --reason died \
    --effective-date 2025-04-19 --evidence-url https://... --dry-run
python -m usa_wa_adapter_legislature.operators.cli \
    --member-id 35410 --kind seated --reason appointed \
    --seat-kind chamber-senate --seat-discriminator 5 \
    --effective-date 2025-06-03 --evidence-url https://...
python -m usa_wa_adapter_legislature.operators.cli --file events.json   # JSON-array batch
python -m usa_wa_adapter_legislature.operators.cli --supersede <id> \
    --member-id 35410 --kind seated --reason appointed \
    --seat-kind chamber-senate --seat-discriminator 5 \
    --effective-date 2025-06-10 --evidence-url https://...   # date-correction of <id>
python -m usa_wa_adapter_legislature.operators.cli --list               # current events
```

## Committee lineage & lifecycle (#124)

WA re-keys standing committees across eras (new WSL `Id` ~each decade), so the same
body appears as several orgs with disparate dated names and no visible lifecycle. The
**objective** half derives in the nightly build: `organizations.active` is true while the
current biennium's roster wire attests a committee (#428). The **judgment** layer is
operator-attested succession links (C2) — which era-`Id` continued / split from / merged
with which — published as the `org_lineage` dataset since #447, which also derives a
succeeded or merged predecessor inactive. Their coherence gates are dbt tests
(`organizations_inactive_have_no_live_members`, `organizations_succeeded_are_inactive`). The
C1/C3 PM producers retired with #314, and the C1a lifecycle windows, C4 invariant unit and C5
candidate report with the canonical tier (#412 PR F). See
[`docs/specs/2026-07-25-committee-lineage-lifecycle-design.md`](specs/2026-07-25-committee-lineage-lifecycle-design.md).

```bash
# C2 — record an operator-attested succession link (the judgment layer). Both --subject and
# --linked are WSL committee Ids that must be registered org keys (usa_wa_legislature:<Id>
# in registry.entity_keys) — standing, Joint or Other alike (an integer Id, negative for some
# Other bodies), never a STRUCTURAL_ORGS id
# (#445: the registry, so a committee first staged links once the nightly registrar binds
# it). A typo is a hard error, not a silent no-op link. App-role DML (writes
# registry.committee_succession_events, and the attestation body to the raw store under
# usa_wa_operator after the commit — its only provenance since #412 PR F; exit 4 if only that
# copy failed, recovered as for operator events above: record the link again as it now
# stands, without --supersede or a batch's supersede_id); provenance is append-only.
# A wrong-successor / year fix is --supersede (a NEW row stamping the prior's superseded_by_id).
# On a supersede: --year sets, --clear-year clears, omitting both inherits the prior's year.
# --dry-run validates + writes, then rolls back — but --list is read-only and commits even
# under --dry-run, which is why this job keeps its own transaction on the #179b harness.
# Exit 2 on a validation failure (unchanged).
python -m usa_wa_adapter_legislature.committees.succession_cli \
    --subject 14294 --linked 28244 --slug succeeded_by --year 2021 \
    --evidence-url https://... [--notes "renamed + re-scoped"]
python -m usa_wa_adapter_legislature.committees.succession_cli --file links.json   # JSON-array batch
python -m usa_wa_adapter_legislature.committees.succession_cli --supersede <id> \
    --subject 14294 --linked 31000 --slug succeeded_by --year 2022 \
    --evidence-url https://...                                    # re-link / year correction
python -m usa_wa_adapter_legislature.committees.succession_cli --supersede <id> \
    --subject 14294 --linked 28244 --slug succeeded_by --clear-year \
    --evidence-url https://...                        # clear the year (vs omit --year = inherit)
python -m usa_wa_adapter_legislature.committees.succession_cli --list               # current links

# Publication: the nightly publishes C2's current links as the
# `org_lineage` dataset since #447 (PM pulls it), so a link recorded here publishes after the next
# pipeline run — and a --supersede retracts the old edge the same way. Gates and contract:
# docs/PIPELINE-CONFORMED-ENTITIES.md § Conformed: committee lineage.
```
