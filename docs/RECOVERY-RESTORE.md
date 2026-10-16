# Recovery — restore and rehearsals

The restore half of [RECOVERY.md](RECOVERY.md) (#434): the procedure, in place and on a
replacement host, and the rehearsals that have exercised it. The backup it restores,
its sandbox, provisioning and dead-man monitor stay in RECOVERY.md.

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
- every key in the `person_crosswalk` / `org_crosswalk` published **at or before the
  dump** (`--datasets-root`, default `USA_WA_DATASETS_ROOT` or `data/datasets`) resolves
  to the same ULID. Not the catalog's latest: a dump older than the newest publish
  (`--object`, or a night the backup failed and the pipeline did not) would read every
  key registered since as missing. A version whose `data.csv` does not match its
  `datapackage.json` hash — a crashed publish — is passed over. A missing key or a
  moved ULID is a failure (an adjudicated key move since that publish reads as one:
  check `registry.adjudications`); a `merged_into` that changed since, or a key
  registered since, is counted only. With no such version — a fresh host has no
  datasets — the check reports `skipped`.

**`--raw-into`** fetches into a new private directory, proves every object hashes to
its name and every manifest to its recorded digest, then rebuilds each source's
`latest.json`. Move it into place as `exedev` — **only where no `raw/` exists**: `mv`
onto an existing directory moves the store *into* it (`raw/usa-wa-raw/…`), which reads
as an empty store. Over a damaged store, move that one aside first; keep it until the
restored one has passed the integrity sweep:

```bash
sudo test -e /home/exedev/usa-wa/raw && sudo mv /home/exedev/usa-wa/raw /home/exedev/usa-wa-raw.damaged
sudo chown -R exedev:exedev /root/usa-wa-raw && sudo chmod -R u=rwX,go=rX /root/usa-wa-raw
sudo test ! -e /home/exedev/usa-wa/raw && sudo mv /root/usa-wa-raw /home/exedev/usa-wa/raw \
  || echo "raw/ still exists — refusing to nest the restored store inside it"
```

### In place — the host survives, the database does not

The likeliest incident. Stop every writer first — `dropdb` and `RENAME` both refuse
while anything is connected — and keep the damaged database until the restored one has
passed its checks:

```bash
cd /home/exedev/usa-wa
sudo systemctl stop usa-wa usa-wa-pipeline.timer usa-wa-backup.timer
systemctl is-active usa-wa-pipeline.service usa-wa-backup.service   # both must read inactive
sudo -u postgres psql -c 'ALTER DATABASE usa_wa RENAME TO usa_wa_damaged'
sudo -u postgres createdb -O usa_wa_owner -E UTF8 --locale=C.UTF-8 -T template0 usa_wa
$R --latest --prefix usa-wa --into usa_wa --run-as postgres     # load + checks; exit 0 or stop
sudo systemctl restart usa-wa-migrate                           # re-applies scripts/grants.sql
sudo -u postgres psql -d usa_wa < scripts/setup-backup-role.sql # database-level CONNECT
sudo systemctl start usa-wa usa-wa-backup.timer
```

**Before restarting `usa-wa-pipeline.timer`**: whatever the registry gained after the
dump is gone, and the next registrar run mints those keys **fresh ULIDs** — not the ones
PM may already hold. The nightly registers at ~08:03 and the backup runs at 10:17, so
the window is a publish between 08:05 and the next 10:17, plus any hand adjudication
since the dump. Compare the newest published `person_crosswalk` / `org_crosswalk`
against the restored registry first; a key there that the registry lacks needs its
ULID carried over by hand before the pipeline runs. Then
`sudo systemctl start usa-wa-pipeline.timer`, and `dropdb usa_wa_damaged` once satisfied.

### A replacement host

The dump carries table owners and grants — not the roles, not the database itself. In
order:

0. The host: clone the repo to `/home/exedev/usa-wa`, `uv sync --locked`, restore
   `/etc/usa-wa/.env` from the password manager, copy the units
   ([README § Deploy](../README.md)) — and enable nothing yet.
1. Postgres 16; `usa_wa_owner` and `usa_wa_app` with the passwords `/etc/usa-wa/.env`
   carries ([DEPLOYMENT.md § DB role topology](DEPLOYMENT.md)); then the database as
   production's is — that owner, UTF8, `C.UTF-8` (checked 2026-10-01; no database-level
   grants or settings to carry):

   ```bash
   sudo -u postgres createdb -O usa_wa_owner -E UTF8 --locale=C.UTF-8 -T template0 usa_wa
   ```
2. `scripts/setup-backup-role.sql`, both key files and `backup.env` ([RECOVERY.md](RECOVERY.md)
   § Provisioning); the tailnet join (RECOVERY.md § The dead-man monitor, step 1) under the same node name, or the
   check-ins stop and the monitor says so.
3. `$R --latest --prefix <old host> --into usa_wa --run-as postgres` — `usa_wa` is new
   and empty here.
4. `sudo systemctl restart usa-wa-migrate` — alembic is already at head; it re-applies
   `scripts/grants.sql`.
5. `$R --raw-into …`, moved into place as above; then
   `sudo systemctl start usa-wa-integrity-sweep.service` and read its journal — one run
   covers the whole store, well inside its 256 MiB slice. (Not a bare
   `python -m clearinghouse_core.raw_integrity`: the unit carries the environment its
   ledger row needs.)
6. `sudo systemctl start usa-wa-pipeline` — rebuilds duckdb, republishes, reloads
   `serving`. Then enable the API and the timers (README § Deploy).

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
  Provisioning block ([RECOVERY.md](RECOVERY.md) § Provisioning: `co-usa-wa-backup@co-gcs`, bucket in `co-gcs-blobs`' location)
  and installed the key `0400 root:root`. The probe, as root on the key file: the bucket
  listed, `probe/20261001T181733Z` was created, and both the overwrite and the delete
  answered **403**.
- **Installed, and the first run by hand (2026-10-01)**: merge `710f882`, `uv sync
  --locked`, the role script (`t`/`f` as in RECOVERY.md § Install and first run, `0 0 0`), units copied. The run
  succeeded, sandboxed, in 104 s: `db/usa-wa/20261001T205616Z.dump` (13,220,714
  bytes, alembic `746b53a33587`, registry rows 18 / 166 / 3,667 / 5,128 / 553 for
  adjudications / succession / entities / keys / operator events) and the whole raw
  store, 1,566 files, none mismatched or unrecognized. Timer enabled after it.
- **The production drill (2026-10-01, 21:14Z)**, that object, on this cluster, with
  nothing scheduled to write in the 8 hours around it:
  - `--into usa_wa_restore_drill` (a new, empty database): fetched, proved and loaded in
    **5.9 s**, `outcome=ok`. Every check passed. The schema version and all five
    registry counts matched. The crosswalk check compared against
    `person_crosswalk` v20260919 (4,596 / 4,596 matched) and `org_crosswalk` v20260924
    (220 / 220), with none missing, reassigned or merge-changed.
  - Beyond the built-in checks, restored against live: 61 tables, 219 indexes and 217
    constraints on both sides, the same alembic head, and **every table's row count
    identical** (122,979 rows). The grants came back with it (`usa_wa_app` DML yes,
    DDL no, owner `usa_wa_owner`).
  - `--raw-into` a scratch directory: 1,566 files (46,850,286 bytes) fetched and proved
    in **86 s**, and `latest.json` rebuilt for all six sources. `diff -r -x '.*'`
    against the live store showed no differences, `latest.json` included, and
    `verify_store` over the restored copy was clean (1,393 objects, 0 mismatched,
    0 missing).
  - Both scratch copies removed.

- **Joined the tailnet (2026-10-02, #455)**: node `usa-wa`, `100.124.127.41`,
  `tag:usa-wa`, no `--ssh`. `http://status:9000/health` answered `production`;
  `status:9001` and `notifier:9000` did not — the ACL grants `:9000` on co-status only.
  Public DNS (GitHub, `storage.googleapis.com`) still resolves through MagicDNS.
- **The dead-man alarm, seen to fire and recover (2026-10-02, #455, CannObserv/status#17)**,
  coordinated live with co-status's agent; co-status figures are from its database
  and journals:
  - Deployed `603e937`; `backup.env` gained the base URL and monitor id.
  - First check-in: run started 20:51:15Z, `backup_checkin_sent status=ok` at
    20:51:23.763Z (202). co-status: `pending → ok`, event `first_checkin`.
  - Alarm: `PATCH` 60/0; the 21:06:38.77Z sweep marked it `missing` and sent *"stopped
    reporting"* (dispatch `01M3Z71HMA31AFZRZ8WH7J1P86`, `succeeded`), received in
    Mailgun and Slack. `PATCH` back to 86400/7200 at 21:07:35.99Z, still `missing`.
  - Recovery: run started 21:08:48Z, check-in 202 at 21:08:56.227Z. co-status: event
    `recovered` at 21:08:55.626Z, dispatch `01M3Z75Q76P2SAGDM421HMD7EJ` accepted by
    notifier; state `ok`; received in Mailgun and Slack. co-status's pending copy of the
    key was shredded after it: `/etc/usa-wa/backup-checkin.key` is the only copy.
  - This side logged the check-in's status code only, not the 202's `dispatches` ids:
    #458, since fixed — `backup_checkin_sent` now carries them.

**Repeating the drill** — the same steps against any night's object:
`sudo -u postgres createdb usa_wa_restore_drill`, `$R --latest --prefix usa-wa --into
usa_wa_restore_drill --run-as postgres`, `$R --raw-into /root/usa-wa-raw-drill`,
`sudo diff -r -x '.*' /home/exedev/usa-wa/raw /root/usa-wa-raw-drill`, then `dropdb`
and `rm -rf` the two. Reading the diff: `Only in /home/exedev/usa-wa/raw…` lines for
objects and manifests newer than the night's backup are expected (an attestation after
10:17, a harvest since), and so is a differing `latest.json` *if* the live store has
runs the backup has not seen. A difference inside any object or manifest both sides
hold, or an `Only in` the restored side, is never expected — stop there. Record the
result here.
