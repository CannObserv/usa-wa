# Recovery

The nightly backup to GCS, the restore, and the runbook around both (#434). Built on
CannObserv/watcher's `docs/RECOVERY.md` (itself after broker#4); where this differs,
it says why.

## What was exposed

Until #434 nothing on this host was backed up. #430 found `/var/backups` empty during
the 2026-09-26 OS patch run; the only recovery points were the dumps it made by hand.

Two things here cannot be rebuilt from anything else:

| What | Where | Why it is not regenerable |
|---|---|---|
| **The registry** | Postgres `registry` schema (~2.8 MB) | `entities` + `entity_keys` hold the **ULIDs** PM's crosswalk is keyed on — re-seeding mints new ones. `adjudications` are merges a person decided; their `merged_into` tombstones are contract (#302 spec). `operator_events` and `committee_succession_events` are attested by hand. |
| **The raw store** | `USA_WA_RAW_ROOT` (`raw/`, ~52 MB) | The input every dataset is rebuilt from. Re-harvesting does not reproduce it: sources drift and disappear, which is why it is archival (#54). Since #412 it also holds every operator attestation's body (`usa_wa_operator`). |

Everything else is derived. `serving` reloads from `data/datasets/`; the datasets and
`data/pipeline.duckdb` rebuild from the raw store plus the registry (one nightly run).
`canonical` is frozen (#412 PR E) and `clearinghouse_core` is run history and
pre-#302 leftovers. The backup dumps the whole database anyway — 13.2 MB, 2.2 s
(#430) — because a one-step restore beats saving 28 MB of `serving`.

**Not here, and kept elsewhere by hand:** `/etc/usa-wa/.env` (the database passwords,
the alert address) belongs in the password manager, never in a bucket. The cluster's
roles are recreated by the runbook below from those passwords, so the role-globals
dump #430 took is not needed for recovery.

## Design

| Property | How |
|---|---|
| **No database credential, no privilege** | The unit runs as its own dynamic user, `usa_wa_backup`, with no capabilities. `pg_dump` and `psql` connect over the local socket by peer auth to the role of the same name — `pg_read_all_data` and nothing else (`scripts/setup-backup-role.sql`). The GCS key arrives as a systemd credential, never in a process environment. |
| **Verified before it ships** | `pg_restore --list` must find a data section for `public.alembic_version` and every registry table — a readable dump of the wrong database is refused — and `pg_restore --data-only` must then read every data block through (`--list` alone passes a truncated archive). That read counts each table's `COPY` rows. |
| **Named by its own time** | `db/<host>/<YYYYMMDDTHHMMSSZ>.dump` — the start of `pg_dump`, when it took its snapshot. A listing is a timeline. |
| **Metadata travels with the dump** | `dumped_at`, `sha256`, `size_bytes`, `alembic_head`, `server_version`, `pg_dump_version`, `toc_entries`, `source_host`, and `registry_rows` — each registry table's row count from the same snapshot, which a restore is checked against. |
| **The raw store, mirrored** | `raw/<source>/objects/<aa>/<sha256>` and `raw/<source>/runs/<run_id>.json`, at the store's own relative paths. Each night uploads what the bucket does not list yet. An object is hashed first and refused if it no longer matches its name — the integrity sweep's finding, which a backup must not launder. `latest.json` is not mirrored (mutable; a restore rebuilds it from the manifests, `RawStore.rebuild_latest`, byte-identical on all six production sources 2026-10-01). No host in the path: content-addressed objects serve any host. |
| **Create, never overwrite or delete** | `if_generation_match=0` in code; `objectCreator` + `objectViewer` at IAM, no `delete`. A 412 is `unchanged` only if the object's recorded sha256 matches; otherwise a collision, and a failure. |
| **Retention is the bucket's** | Lifecycle deletes `db/` at 90 days and `probe/` at 1; `raw/` is kept indefinitely, like the store it mirrors. Soft-delete left on. A compromised host cannot erase its own history. |
| **Two halves, one verdict** | A failed dump still mirrors the raw store, and vice versa; either failing exits 1 → `OnFailure=` email. A missing bucket name or a misplaced key exits 2 and ships nothing. |

**RPO is 24 hours** — one run a night, at 10:17 UTC, two hours after the 08:00
pipeline whose registrar is what changes the registry. An operator attestation made
during the day is in the next morning's backup.

**Silence is not yet alarmed.** A failed run emails; a run that never starts (timer
disabled, unit not installed) does not. Watcher closes that with a co-status dead-man
check-in; this repo has no co-status integration yet.

## The sandbox, and why it is this shape

`deploy/usa-wa-backup.service` runs repo code nightly, holding the one key that can
write the bucket. It follows watcher#297 line for line, plus three things this repo
needs:

| | How |
|---|---|
| **Its own user** | `DynamicUser=yes`, `User=usa_wa_backup`: a uid allocated for the run and released after it. |
| **No capabilities** | `CapabilityBoundingSet=` empty, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `PrivateTmp=yes` (the dump is written there), kernel/namespace/personality protections, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`. |
| **Key as a credential** | `LoadCredential=gcs:/etc/usa-wa/co-usa-wa-backup.json`; the SDK gets the private copy's path, `GOOGLE_APPLICATION_CREDENTIALS=%d/gcs`. A stray `GOOGLE_APPLICATION_CREDENTIALS` in `backup.env` would win over that line and aim the job at the root-only original — the job refuses it (exit 2) rather than fail "Permission denied", which reads as a reason to loosen the key. |
| **An empty home, minus the secrets** | `ProtectHome=tmpfs` + `BindReadOnlyPaths=/home/exedev/usa-wa`: the venv and the raw store and nothing else of exedev's. **The checkout's `.env` is mode 0644** and carries the agent tokens, and each worktree has its own, so `InaccessiblePaths=` masks `.env` and `.worktrees/`. |
| **The guards, inside the sandbox** *(usa-wa)* | `assert-main-checkout.sh` (#87) and `assert-venv-integrity.sh` (#279) run as the dynamic user, which does not own the checkout — git's ownership check refuses it ("dubious ownership") unless the checkout is named safe. `GIT_CONFIG_COUNT/KEY_0/VALUE_0` names it in command scope; no gitconfig is read or written. Verified in a transient unit 2026-10-01: both guards exit 0 with it, the branch guard exits 1 without. |
| **Its own configuration only** *(usa-wa)* | `EnvironmentFile=/etc/usa-wa/backup.env` (the bucket name) — never `/etc/usa-wa/.env`, whose database URLs this job must not have. `USA_WA_RAW_ROOT` is set in the unit. |
| **The venv's interpreter** | `.venv/bin/python -m usa_wa_api.backup.run`, not `uv run`: uv wants a writable cache. The venv's python links to `/usr/bin/python3.12`, outside the hidden `/home`; `scripts/tests/test_backup_unit.py` fails on a host where it would not. |

**A dump the role cannot read fails loudly.** A table under row security or a large
object is a `pg_dump` error, never a thinner dump. Production has neither (checked
2026-10-01); the role script's report counts both every time it runs.

## Provisioning — needs the GCP project owner

The node has no `gcloud` and no credential that can create any of this. Run from a
workstation holding `storage.admin`, `iam.serviceAccountAdmin` and
`iam.serviceAccountKeyAdmin` on `co-gcs` (the owner has all three). Mirrors broker's
as-run block, with the lifecycle scoped by prefix and the location read from
`co-gcs-blobs` rather than typed:

```bash
PROJECT=co-gcs
BUCKET=co-gcs-usa-wa-backup
SA=co-usa-wa-backup
SA_EMAIL="$SA@$PROJECT.iam.gserviceaccount.com"
LOCATION=$(gcloud storage buckets describe gs://co-gcs-blobs --format="value(location)")
echo "location: $LOCATION"    # must print one; an empty value would let create pick a default

gcloud storage buckets create "gs://$BUCKET" --project="$PROJECT" --location="$LOCATION" \
    --uniform-bucket-level-access --public-access-prevention
cat > /tmp/lifecycle.json <<'EOF'
{"rule": [
  {"action": {"type": "Delete"}, "condition": {"age": 90, "matchesPrefix": ["db/"]}},
  {"action": {"type": "Delete"}, "condition": {"age": 1, "matchesPrefix": ["probe/"]}}
]}
EOF
gcloud storage buckets update "gs://$BUCKET" --lifecycle-file=/tmp/lifecycle.json
gcloud storage buckets describe "gs://$BUCKET" --format="yaml(location, lifecycle_config)"
#   gcloud storage's key names; the API's camelCase spellings print nothing (broker)

gcloud iam service-accounts create "$SA" --project="$PROJECT" \
    --display-name="usa-wa backup writer (usa-wa#434)"
for role in roles/storage.objectCreator roles/storage.objectViewer; do
    gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
        --member="serviceAccount:$SA_EMAIL" --role="$role"
done
gcloud storage buckets get-iam-policy "gs://$BUCKET" \
    --flatten="bindings[].members" --format="table(bindings.role, bindings.members)" \
    | grep "$SA_EMAIL"    # exactly objectCreator + objectViewer (no --filter on this command)

gcloud iam service-accounts keys create co-usa-wa-backup.json --iam-account="$SA_EMAIL"
scp co-usa-wa-backup.json usa-wa.exe.xyz:    # however #430's dump came off the node
shred -u co-usa-wa-backup.json
```

Bucket-level bindings only — nothing at the project. Soft-delete left at its
seven-day default (a second net against an administrator's mistake); versioning off.

`raw/` has no lifecycle rule on purpose: the store it mirrors never deletes.

On the host — the key `0400 root:root`, read only by systemd; `backup.env` holds no
secret and **no `GOOGLE_APPLICATION_CREDENTIALS`**:

```bash
sudo install -m 0400 -o root -g root ~/co-usa-wa-backup.json /etc/usa-wa/co-usa-wa-backup.json
shred -u ~/co-usa-wa-backup.json
sudo install -m 0644 -o root -g root /dev/null /etc/usa-wa/backup.env
echo 'USA_WA_BACKUP_BUCKET=co-gcs-usa-wa-backup' | sudo tee /etc/usa-wa/backup.env >/dev/null
```

## Install and first run

After the merge, on the prod checkout (`uv sync --locked` is required: the backup
brings `google-cloud-storage`). The role first, once per cluster; its report must read
`t` for `login`, `inherit`, `no_password`, `reads_all_data`, `can_connect`, `f` for
the rest, and three zeros:

```bash
cd /home/exedev/usa-wa && git pull && uv sync --locked
sudo -u postgres psql -d usa_wa < scripts/setup-backup-role.sql
sudo cp deploy/usa-wa-backup.service deploy/usa-wa-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl start usa-wa-backup.service          # one run, by hand
sudo journalctl -u usa-wa-backup.service -n 30      # backup_shipped, then outcome=ok
sudo systemctl enable --now usa-wa-backup.timer     # only once the run above succeeded
```

The first run uploads the whole raw store (1,566 files, 47 MB on 2026-10-01); later nights upload a
handful. `243/CREDENTIALS` is the key file missing; `203/EXEC` an interpreter the empty
`/home` hides; `FATAL: role "usa_wa_backup" does not exist` the role script not run.

**Prove the grant is create-only by observation** — on an object of the probe's own,
which the `probe/` lifecycle rule removes. Under `objectCreator` + `objectViewer` both
the overwrite and the delete must answer **403**:

```bash
sudo bash -c "GOOGLE_APPLICATION_CREDENTIALS=/etc/usa-wa/co-usa-wa-backup.json .venv/bin/python -" <<'PY'
from datetime import UTC, datetime
from google.api_core.exceptions import Forbidden
from google.cloud import storage
bucket = storage.Client().bucket("co-gcs-usa-wa-backup")
blob = bucket.blob(f"probe/{datetime.now(UTC):%Y%m%dT%H%M%SZ}")
blob.upload_from_string(b"probe", if_generation_match=0)  # the create: must succeed
for attempt, act in (("overwrite", lambda: blob.upload_from_string(b"again")),
                     ("delete", blob.delete)):
    try:
        act()
        print(f"{attempt}: ALLOWED — the grant is too wide")
    except Forbidden:
        print(f"{attempt}: 403 — create-only holds")
PY
```

## Restore

`python -m usa_wa_api.backup.restore`, run by hand as root (the key is root-only) from
the checkout, with the key and bucket in its environment:

```bash
R="sudo env GOOGLE_APPLICATION_CREDENTIALS=/etc/usa-wa/co-usa-wa-backup.json \
   USA_WA_BACKUP_BUCKET=co-gcs-usa-wa-backup .venv/bin/python -m usa_wa_api.backup.restore"

$R --list                                              # every host's dumps, with metadata
$R --latest --prefix usa-wa --download-only /root/usa-wa-restore   # fetch + prove only
$R --latest --prefix usa-wa --into <empty db> --run-as postgres    # load + check
$R --raw-into /root/usa-wa-raw                         # the raw store, into a new directory
```

`--latest` needs `--prefix`: the restoring host is rarely the one that shipped, and
its own name is the one prefix never wanted. `--object <key>` picks a specific dump.
A name more than ten minutes later than the bucket's own creation time for it is
`SUSPECT` in `--list` and passed over by `--latest` (a skewed clock, or a forgery
planted to own `--latest`).

**`--into` refuses a database that holds anything** — `--into usa_wa` by mistake loads
nothing. It fetches into a private directory, proves the sha256 and re-runs the
backup's verification (whose row counts must equal the recorded ones), loads in one
transaction on stdin as `postgres`, then checks, failing the run on any finding:

- the schema version equals the dump's `alembic_head`;
- every registry table's row count equals the dump's `registry_rows`;
- every key in the newest published `person_crosswalk` / `org_crosswalk`
  (`--datasets-root`, default `USA_WA_DATASETS_ROOT` or `data/datasets`) resolves to
  the same ULID. A missing key or a moved ULID is a failure; a `merged_into` that
  changed since the publish, or a key registered since, is counted only. On a fresh
  host with no datasets the check reports `skipped`.

**`--raw-into`** fetches into a new private directory, proves every object hashes to
its name and every manifest to its recorded digest, then rebuilds each source's
`latest.json`. Move it into place as `exedev`:

```bash
sudo chown -R exedev:exedev /root/usa-wa-raw && sudo chmod -R u=rwX,go=rX /root/usa-wa-raw
sudo mv /root/usa-wa-raw /home/exedev/usa-wa/raw
```

### A replacement host

The dump carries table owners and grants, not the roles. In order:

1. Provision Postgres; create `usa_wa_owner` and `usa_wa_app` with the passwords from
   the password manager's copy of `/etc/usa-wa/.env`, and the database owned by the
   owner role ([DEPLOYMENT.md § DB role topology](DEPLOYMENT.md)).
2. `scripts/setup-backup-role.sql`, the key and `backup.env` (above).
3. `$R --latest --prefix <old host> --into usa_wa --run-as postgres` — `usa_wa` is new
   and empty here.
4. `scripts/grants.sql`, as DEPLOYMENT.md says — idempotent.
5. `$R --raw-into …`, moved into place as above; then the integrity sweep
   (`python -m clearinghouse_core.raw_integrity --expect-objects`).
6. `sudo systemctl start usa-wa-pipeline` — rebuilds duckdb, republishes, reloads
   `serving`. Then the API.

## Rehearsals

- **Parsers against real tools (2026-10-01)**: `tests/test_backup_rehearsal.py` (db
  tier) dumps the test database with the real `pg_dump`, verifies it with the real
  `pg_restore`, checks the source against its own dump, and runs the crosswalk check
  through the real `psql --csv` — green on first run. It does not load a second
  database: the test role cannot create one.
- **`rebuild_latest` on production's store (2026-10-01)**: on a copy of
  `/home/exedev/usa-wa/raw`, all six sources' rebuilt `latest.json` were
  byte-identical to the live ones.
- **The guards inside the sandbox (2026-10-01)**: a transient `systemd-run` unit with
  this unit's `DynamicUser`/`ProtectHome`/`BindReadOnlyPaths`/`ProtectSystem` ran both
  guards and the venv interpreter: exit 0 with the `GIT_CONFIG_*` lines, branch guard
  exit 1 without.
- **Provisioned, and create-only by observation (2026-10-01)**: the owner ran the
  Provisioning block (`co-usa-wa-backup@co-gcs`, bucket in `co-gcs-blobs`' location)
  and installed the key `0400 root:root`. The probe, as root on the key file: the bucket
  listed, `probe/20261001T181733Z` was created, and both the overwrite and the delete
  answered **403**.
- **Pending — the production drill.** Needs the role (Install). Then: the first
  hand-started run, and a
  restore of that night's object into a scratch database on this cluster
  (`sudo -u postgres createdb usa_wa_restore_drill`, `$R --latest --prefix usa-wa --into
  usa_wa_restore_drill --run-as postgres`, then `dropdb`) plus `$R --raw-into` a scratch
  directory, compared with `diff -r -x '.*'` against the live store. Record the result
  here.
