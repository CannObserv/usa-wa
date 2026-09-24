# The conformed tier — crosswalks + entities

The identity surface of the #302 pipeline's conformed tier, split out of
[`PIPELINE-CONFORMED.md`](PIPELINE-CONFORMED.md), which keeps the tenure spans and the
citations chain built on it.

## Conformed: crosswalks + entities (#309, in progress)

`models/conformed/`: `person_crosswalk` / `org_crosswalk` (the registry's
published identity surface — every natural key, its entity ULID, and the
`merged_into` tombstone, read via `usa_wa_pipeline.registry_read`; empty only
under `USA_WA_PIPELINE_HERMETIC=1` — a missing `DATABASE_URL` fails the build,
[`PIPELINE-PUBLICATION.md`](PIPELINE-PUBLICATION.md)) and `persons` /
`organizations` (one row per LIVE entity; logic in
`usa_wa_pipeline.conformed.entities` — person names roster > WSL > PDC with
newest-attestation-wins, org attributes from the newest biennium's roster wire,
meeting-ref fallback for Joint/`Other`, and the synthesized structural orgs —
legislature, chambers, parties — from `usa_wa_common.orgs.STRUCTURAL_ORGS`).
An organization's name goes through the same blank screen as a person's
(#364 CR 5); its `acronym` deliberately does not, because 35 are space-padded
in the wire and trimming them would restate 35 published values.

**A merge re-points, it does not delete** (#366). Every conformed reader of the
crosswalk attributes a tombstoned entity's keys to its survivor — the tombstone
is the published crosswalk's only re-point signal — through one shared walk,
`conformed.crosswalk` over `clearinghouse_core.registry.resolve_merged`. It is
one walk because it used to be four, and the divergence between them IS #366:
the spans and citations readers followed the tombstone, `_live_entities` dropped
the loser's rows, and the registry's first real merge published Denny Heck's
tenure span and his roster citation under the survivor while leaving his NAME
behind — `persons` carried no name for him at all, worse than the duplicate the
merge was resolving. `merge_map` also screens a tombstone as a non-blank string
rather than `is not None`, because the crosswalk models pin `merged_into` to
pandas' `string` dtype and its null is `pd.NA`.

`profiles.yml` pins `threads: 1`: threaded Python models race first-imports of
the workspace packages. Verified on the real archive 2026-09-10: 3,134 persons
(2,999 roster-named / 135 WSL / **no gap**) and 219 orgs, type distribution
matching canonical exactly. Was 3,135 with one nameless entity — #366 merged
Denny Heck's WSL id with the roster identity his 1977-85 listings minted, which
is the registry's first real merge tombstone. Assignments (the span
engine as a Python model) landed next — see [`PIPELINE-CONFORMED.md`](PIPELINE-CONFORMED.md); roles/seats complete the
layer.

**A blank is not a name** (#364). `GetSponsors` answers with a name-blanked
STUB for a superseded / departed (member, chamber-tenure) — a real `Id`, `Name`
a single space, no first/last — the shape `normalize.members.is_person` has
always screened on the canonical path. Survivorship did not: `' '` is truthy,
so the stub read as the member's newest attestation and Tina Orwall, Tim
Sheldon, Robert Sutherland and Simon Sefzik published `' '` as their legal name.
Nothing here noticed; power-map#497 found it downstream a week later (the
snapshot it read, `v20260903T085702Z`, against a 2026-09-10 report), and
under the #490 contract the producer owns a person's legal name. `entities._name`
now strips every source's name field and reads blank as ABSENT — falling through
to the next link rather than stopping there, since a source that cannot name
someone does not veto the ones below it. It trims real names too (`'Marlo
Braun '` from WSL, `'MICHAEL JAMES BAUMGARTNER '` from PDC). Deliberately at
this tier, not in staging: staging re-parses the archive and holds no policy,
and nulling the stub there would erase the evidence that the wire answered with
one.

**And an annotation is not a name either** (#378). The roster's name column
prints two unrelated things in parentheses: name content — marital forms
(`Agnes (Mrs. Thomas E.) Kehoe`) and legal-name glosses (`Jack (John T.)
Dootson`) — and facts about a person's *service*, typeset inside the name
(`(Resgnd Dec. 31, 1982)`, `(On leave of absence for military duty Jan. 8, 1991
to April 18, 1991)`). `entities._display_name` removes the second class through
`usa_wa_common.names.strip_tenure_notes` and leaves the first untouched.

The narrowness is the point, in both directions. Reusing `strip_non_name_parts`
— the matching screen — would have cut `A. L. “Slim” Rasmussen` to `A. L.
Rasmussen` and rendered `Mrs. Irwin LeCocq (Mary)` as `Irwin LeCocq`, her
husband's name published as hers; a screen whose output nothing reads may
over-strip, a published one may not. And **whether a woman should be published
under a marital print form at all is deliberately left unanswered** — it is a
real editorial question, and leaving those forms alone keeps it a decision
someone makes rather than one a strip makes silently.

It mattered because survivorship is roster > WSL: merging #378's 17 duplicate
pairs made the roster name win, so without this `Myron “Mike” Kreidler (On leave
of absence …)` would have become the published legal name of a live,
power-map-resolved legislator — the #364 shape again, one issue later. The fold
still reads the RAW printed name, so no registry key moves; only what publishes
is stripped. `tests/persons_unannotated.sql` gates it at zero.

**Nor is a seat designator** (#367). The roster prints #229's Position signal
into the name cell on LD-19/LD-39 — `John Wynne – 39A` — and 8 of 3,117 rows
published it. `_display_name` applies `strip_position_suffix` *after*
`strip_tenure_notes`, because the suffix anchors on end-of-string and a name
carrying both loses it only once the note is gone.
`tests/persons_no_seat_suffix.sql` gates the designator's *shape* at zero.

Wiring, not a rule: `strip_position_suffix` has been public since CR #88 saying
it exists *"so the display-name minter shares one definition of what counts as
seat metadata"*, and the minter never called it. A screen that is written and
documented as shared can still be silently uninvoked — only a gate over the
output notices, and power-map#499 noticed first.

The guard that outlives the fix is `tests/persons_named.sql`, gated at zero:
blank, untrimmed, or a `name_full`/`name_source` pair with one side missing
fails `dbt build`. It is deliberately NOT a `not_null` test on `name_full` nor a
`required` constraint in the published datapackage — the question #364 raises.
The instance that first proved the point is gone (#366 named Denny Heck through
a merge), but the SHAPE is not: a registry entity no source attests is one
adjudication or one retired wire away at any time, and requiring a name would
wedge the nightly the day it recurs. Permitting a null costs the gate nothing —
it still refuses every blank.

**And one entity per human** (#378 step 4). `persons` is seeded from namespaces
that mint independently — the legislature's numeric member id, the roster PDF's
`<fold>:<year>`, the PDC's filer id — and nothing reconciled them, so 17 pairs
published as two people each with no `merged_into` in `person_crosswalk` to say
otherwise. power-map had already resolved every member-id entity and planned
every roster twin as a **create**; applying the subscription would have minted
17 duplicate people, Patty Murray and Jack Metcalf among them. A consumer
reading the dataset is what caught it.

`models/conformed/person_name_collisions.py` is the guard that moves that
discovery upstream: a thin binder over `usa_wa_pipeline.conformed.namesakes`,
grouping live entities by `identity_fold` and emitting every row of every
un-allowlisted collision. `tests/persons_distinct_identities.sql` gates it at
zero, so the 18th fails `dbt build` rather than shipping.

**A python model, not a SQL test**, unlike its two neighbours. The grouping key
is `identity_fold` — Python, in `usa-wa-adapter-legislature` — and `persons`
publishes only `(entity_id, name_full, name_source)`, so there is no fold in
SQL's reach; re-deriving one there is the second implementation CR 155
consolidated out of `usa_wa_common.names`. The parity probes were the other
candidate home and run *after* `dataset-publish`: they can report that a bad
dataset shipped, never stop it shipping, which is precisely how the 17 got out.
The model materializes (it is not in `PUBLISHED_DATASETS`) because on a red
build the table is the hand-review work order — both sides of every pair, since
which id survives a merge is an adjudication and not something a gate may
presume.

**14 of the 17, and the allowlist is three.** The fold keeps middle initials, so
`Stanley Johnson` / `Stanley C. Johnson` and the two Salings are invisible here.
Loosening it to first-given-plus-surname recovers two and collides 42 groups over
88 rows — including `N. B. Atkinson` / `N. P. Atkinson`, which
`roster_pdf/identity.py` documents as distinct on five grounds; the miss is the
bought half of that trade. What legitimately collides is three folds: every
`IDENTITY_SPLITS` key **by derivation**, since a roster split mints two entities
from one fold on purpose and restating them here would break the nightly the next
time one is added; plus `bobmccaslin` and `briansullivan`, each carrying the
evidence that settled it — an allowlist entry tells the build to stop looking
forever, so an unexplained one is a duplicate someone waved through.

This does not fix the cause. `registry_seed` still has no `deleted_at` filter,
so a producer-side soft-delete is still ignored and an 18th duplicate can still
be *minted*; the gate catches it at build time. That split is deliberate — the
gate survives `canonical.*` retiring at #314, the seed fix does not.
