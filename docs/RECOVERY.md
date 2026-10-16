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
`clearinghouse_core` is run history (`job_runs`) plus the source and jurisdiction
vocabulary that `coverage_seed` (nightly) and `seed_jurisdictions` (by hand) reassert; the
`canonical` schema and the Postgres provenance tables were dropped in #412 PR F. The
backup dumps the whole database anyway — 13.2 MB, 2.2 s (#430) — because a one-step restore beats saving 28 MB of `serving`.

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
| **The raw store, mirrored** | `raw/<source>/objects/<aa>/<sha256>` and `raw/<source>/runs/<run_id>.json`, at the store's own relative paths. Each night uploads what the bucket does not list yet. An object is hashed first and refused if it no longer matches its name — the integrity sweep's finding, which a backup must not launder. `latest.json` is not mirrored (mutable; a restore rebuilds it from the manifests, `RawStore.rebuild_latest`, byte-identical on all six production sources 2026-10-01). Any other file fails the run rather than going unshipped unnoticed. No host in the path: content-addressed objects serve any host. |
| **Create, never overwrite or delete** | `if_generation_match=0` in code; `objectCreator` + `objectViewer` at IAM, no `delete`. A 412 is `unchanged` only if the object's recorded sha256 matches; otherwise a collision, and a failure. |
| **Retention is the bucket's** | Lifecycle deletes `db/` at 90 days and `probe/` at 1; `raw/` is kept indefinitely, like the store it mirrors. Soft-delete left on. A compromised host cannot erase its own history. |
| **Two halves, one verdict** | A failed dump still mirrors the raw store, and vice versa; either failing exits 1 → `OnFailure=` email. A missing bucket name or a misplaced key exits 2 and ships nothing. |
| **Failure is loud, silence too** (#455) | Every run but a `--dry-run` checks in to a co-status dead-man monitor: `ok` with the night's summary, `alert` with what failed. The monitor alarms when neither arrives in 26 hours. § The dead-man monitor. |

**RPO is 24 hours** — one run a night, at 10:17 UTC, two hours after the 08:00
pipeline whose registrar is what changes the registry. An operator attestation made
during the day is in the next morning's backup.

**Silence is alarmed** (#455). A failed run emails, through `OnFailure=`. A run that
never starts — timer disabled, unit never installed on a new host, a key file missing
(`243/CREDENTIALS`, before any of the job runs), a job wedged until its timeout —
emails nothing; it sends no check-in either, and co-status reports it missing. The
check-in never fails the run and never changes its exit status
(`usa_wa_api.backup.checkin`, after watcher's `src/ops/checkin.py`).

## The sandbox, and why it is this shape

`deploy/usa-wa-backup.service` runs repo code nightly, holding the one key that can
write the bucket. It follows watcher#297 line for line, plus three things this repo
needs:

| | How |
|---|---|
| **Its own user** | `DynamicUser=yes`, `User=usa_wa_backup`: a uid allocated for the run and released after it. |
| **No capabilities** | `CapabilityBoundingSet=` empty, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `PrivateTmp=yes` (the dump is written there), kernel/namespace/personality protections, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`. |
| **Keys as credentials** | `LoadCredential=gcs:/etc/usa-wa/co-usa-wa-backup.json`; the SDK gets the private copy's path, `GOOGLE_APPLICATION_CREDENTIALS=%d/gcs`. A stray `GOOGLE_APPLICATION_CREDENTIALS` in `backup.env` would win over that line and aim the job at the root-only original — the job refuses it (exit 2) rather than fail "Permission denied", which reads as a reason to loosen the key. The check-in key is the second, `checkin-key:/etc/usa-wa/backup-checkin.key` (#455), read by the job from `$CREDENTIALS_DIRECTORY` and never from the environment. **Both files must exist**: systemd 255 fails a missing source `243/CREDENTIALS` and ignores an empty `SetCredential=` fallback, so the check-in key is an **empty** file until the monitor exists — read as unconfigured, with a warning each night. |
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
# The check-in key: empty until the dead-man monitor exists, but it must exist (#455).
sudo install -m 0400 -o root -g root /dev/null /etc/usa-wa/backup-checkin.key
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
handful. `243/CREDENTIALS` is a key file missing — either one; the check-in key may be
empty, never absent; `203/EXEC` an interpreter the empty `/home` hides;
`FATAL: role "usa_wa_backup" does not exist` the role script not run. Until the
dead-man monitor is wired, every run logs `backup_checkin_unconfigured` — expected.

**Upgrading a host that predates #455:** create the empty check-in key **before**
copying the new unit, or the next run fails `243/CREDENTIALS`:

```bash
sudo install -m 0400 -o root -g root /dev/null /etc/usa-wa/backup-checkin.key
sudo cp deploy/usa-wa-backup.service /etc/systemd/system/ && sudo systemctl daemon-reload
```

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

## The dead-man monitor — needs the tailnet and co-status

`co-usa-wa-backup` (id `01M3YZA6ERMXRM6W5P5XKGFR48`), in co-status tenant
`co-usa-wa`, at `http://status:9000` — CannObserv/status#17; the
shape of watcher's `co-watcher-backup` (its RECOVERY.md § Provisioning; co-status's
own runbook is CannObserv/status `docs/RUNBOOK.md`, its API `docs/reference/monitors.md`).

| Field | Value | Why |
|---|---|---|
| `interval_seconds` | `86400` | one run a night |
| `grace_seconds` | `7200` | the timer's 10-minute jitter plus the unit's one-hour timeout — a run killed by it cannot check in |
| `renotify_seconds` | `86400` | repeat daily while missing, and remind daily while a fault lasts — every failed night still reports (co-status#28, #487) |
| `channel_ids` | co-status's tenant channels in notifier that should reach the usa-wa operator | checked against notifier on every write |
| `title_template` / `body_template` | `usa-wa backup {{ outcome }} on {{ source_host }}` / `{{ error }}` | rendered for an `alert` only |
| `enabled` | `true`, stated | a disabled monitor still forwards `alert`s but never alarms on silence — it looks wired |

**What a check-in carries**, for those templates: an `alert` sends `source_host`,
`outcome` (`failed`) and `error` (every failed half, `; `-joined, or the config
error); an `ok` sends `source_host`, `outcome` (`ok`), `object`, `dumped_at`,
`size_bytes`, `alembic_head`, `raw_uploaded`, `raw_local`.

**What co-status sends** (co-status#28, #487; mechanics in its `monitors.md` § Faults):
every failed night reports, even 23h50m apart; the first `ok` after them sends *"has
cleared"*; a second `alert` within 12 h of a reported one (a same-day retry) sends
nothing.

**Reading `backup_checkin_sent`** (#458): `dispatches` holds the 202's notifier
dispatch ids, never the rendered report. `[]` on a nightly `alert` means nothing went
out (`last_report_status` is `not_accepted`) — unless it was such a retry. On `attempt`
`2`, `[]` may be a replay of a report the first attempt sent. `null`: the body did not
say; the check-in still landed.

**All three or none** — `USA_WA_BACKUP_CHECKIN_BASE_URL` and `USA_WA_BACKUP_MONITOR_ID`
in `backup.env`, and a non-empty key file. Half a configuration logs
`backup_checkin_config_error` naming what is missing and checks in nothing. A
`tenant_id` where the monitor id belongs is a 404 (`backup_checkin_rejected`): both are
ULIDs in the monitor's JSON.

**1. The tailnet** (operator). co-status binds its tailnet address alone, and this VM is
not on the `cannobserv.org.github` tailnet. Needs: an auth key, `tag:usa-wa` only,
pre-approved, **not** ephemeral (an ephemeral node vanishes on a clean shutdown and
takes its grants with it); an ACL row `tag:usa-wa → tag:status:9000` and nothing more;
no `tag:usa-wa` source in any `ssh` or `:22` rule. Then, on this host:

```bash
sudo install -m 600 /dev/null /run/ts.key
sudo tee /run/ts.key >/dev/null <<< 'tskey-auth-...'   # tag:usa-wa, pre-approved, NOT ephemeral
sudo systemctl enable --now tailscaled
sudo tailscale up --auth-key=file:/run/ts.key --hostname=usa-wa
sudo shred -u /run/ts.key
test "$(curl -s http://status:9000/health | jq -r .environment)" = production && echo ok
```

Check `environment`, never only that it answers: a check-in landing on co-status's dev
API (`:9001`) leaves production reporting a healthy job dead. The unit orders
`After=tailscaled.service` — ordering only, so a boot-time catch-up run does not race
MagicDNS, and a Tailscale restart never touches the backup.

**2. The tenant, the key and the monitor** (co-status host, CannObserv/status RUNBOOK §
Cutover step 1 — for a new monitor there is nothing to import):

```bash
. scripts/load_env.sh
out=$(STATUS_ALLOW_PROD_DB=1 uv run python scripts/seed_tenant.py co-usa-wa co-usa-wa-checkin production)
grep -v '^raw_key=' <<< "$out"
sudo install -m 400 -o root -g root /dev/null /etc/status/pending/co-usa-wa.key
sed -n 's/^raw_key=//p' <<< "$out" | sudo tee /etc/status/pending/co-usa-wa.key >/dev/null
unset out
sudo cat /etc/status/pending/co-usa-wa.key | { read -r KEY
  printf 'X-API-Key: %s\n' "$KEY" | curl -sX POST http://status:9000/api/v1/monitors -H @- \
    -H 'Content-Type: application/json' -d '{
      "name": "co-usa-wa-backup", "interval_seconds": 86400, "grace_seconds": 7200,
      "renotify_seconds": 86400, "channel_ids": ["<channel id>", "..."],
      "title_template": "usa-wa backup {{ outcome }} on {{ source_host }}",
      "body_template": "{{ error }}", "enabled": true}'; } | jq '{id, state, enabled}'
```

The monitor's clock starts at creation (`pending`), so it alarms in 26 hours if step 3
never happens — by design.

**3. The key across, terminal to terminal** (this host). Read from the co-status browser
terminal (`sudo cat /etc/status/pending/co-usa-wa.key`), entered at a prompt so it
reaches neither shell history nor argv, through `tee` so the file keeps `0400 root:root`:

```bash
read -rsp 'check-in key: ' KEY; echo
printf '%s' "$KEY" | sudo tee /etc/usa-wa/backup-checkin.key >/dev/null; unset KEY
sudo tee -a /etc/usa-wa/backup.env >/dev/null <<'EOF'
USA_WA_BACKUP_CHECKIN_BASE_URL=http://status:9000
USA_WA_BACKUP_MONITOR_ID=<monitor id>
EOF
sudo systemctl start usa-wa-backup.service
sudo journalctl -u usa-wa-backup.service -n 30 | grep backup_checkin   # backup_checkin_sent
```

Then on co-status: the monitor's `state` is `ok`, `last_checkin_at` fresh; then
`sudo shred -u /etc/status/pending/co-usa-wa.key`.

**4. See the alarm fire** before relying on it, in this order — no run between the
first `PATCH` and the second, since any check-in pushes the deadline out:

1. On co-status, `PATCH` the monitor to `{"interval_seconds": 60, "grace_seconds": 0}`;
   within a sweep (60 s), *"[co-status] co-usa-wa-backup has stopped reporting"* must
   reach the channels.
2. `PATCH` it straight back to `{"interval_seconds": 86400, "grace_seconds": 7200}`. A
   `PATCH` never changes state, so it stays `missing`. Back *before* the run, or a slow
   `PATCH` lets the 60-second window lapse again after the recovery: a second false alarm.
3. `sudo systemctl start usa-wa-backup.service` here. The check-in that ends an outage
   dispatches *"has recovered"* inside the same request (~10 s after the start), not on
   the next sweep; its id is in that run's `backup_checkin_sent` `dispatches`.

Record the result under [RECOVERY-RESTORE.md](RECOVERY-RESTORE.md#rehearsals) § Rehearsals.

## Restore and rehearsals

The restore procedure — in place, or onto a replacement host — and the record of every
rehearsal: [RECOVERY-RESTORE.md](RECOVERY-RESTORE.md).
