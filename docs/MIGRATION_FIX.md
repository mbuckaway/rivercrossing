# MIGRATION_FIX — v2→v3 team-logo migration is not idempotent

- **Status:** open, deferred (no one is running the program yet)
- **Date:** 2026-09-10
- **Area:** `src/rivercrossing/store/migrations.py` (`_migrate_v2_to_v3`, `migrate`)
- **Severity:** blocking at startup for any database left in the inconsistent state below

## Summary

`_migrate_v2_to_v3` unconditionally runs `ALTER TABLE entry DROP COLUMN logo_png`. If a
database's `schema_version` ledger still reads `2` while `entry.logo_png` has already been
dropped — or was never present — the `ALTER` raises `sqlite3.OperationalError: no such column:
"logo_png"`, which aborts `Store.open()` and the app cannot start.

## Symptom

Launching the app (via `scripts/run.sh` or the bundle) against such a database fails with:

```
File ".../src/rivercrossing/store/migrations.py", line 97, in _migrate_v2_to_v3
    conn.execute("ALTER TABLE entry DROP COLUMN logo_png")
sqlite3.OperationalError: no such column: "logo_png"
```

Outer frame, from the per-invocation structured log
(`~/Library/Application Support/RiverCrossing/rivercrossing-<YYYYMMDD-HHMMSS>.log` — one NDJSON log per
launch, whose crash record carries the formatted traceback in a `traceback` array; the separate
plain-text `rivercrossing.log` crash log no longer exists):

```
File ".../src/rivercrossing/ui/app.py", line 4137, in main
    store = Store.open(default_db_path(_resolve_db_path(db_path)))
File ".../src/rivercrossing/store/__init__.py", line 585, in open
    migrate(conn)
File ".../src/rivercrossing/store/migrations.py", line 146, in migrate
    MIGRATIONS[target - 1](conn)
```

Because `migrate()` catches the `sqlite3.Error`, rolls back, and re-raises, `Store.open()` never
returns and the app shows "RiverCrossing could not start".

## Observed database state

Inspected read-only from the affected per-user database:

```
schema_version : [(1, 2)]           # ledger says v2
entry columns  : id, ride_id, plate, display_name, type, team_size, status, dnf_at, notes
                                    # -> NO logo_png
ride  columns  : ... logo_png ...   # ride.logo_png is a different column and is correct
```

So the row and the schema disagree: the column is gone but the ledger never advanced to `3`.

## Root cause

Two distinct problems, both worth fixing:

1. **The migration is not idempotent.** It assumes `entry.logo_png` exists. A database can reach
   v2 without the column — e.g. one created before the column was added to the v1 baseline in
   `src/rivercrossing/store/schema.py:136`, or one left half-migrated by an earlier run. The
   `DROP COLUMN` then hard-fails instead of being a no-op.

2. **Ledger/DDL atomicity is suspect.** `migrate()` wraps each step in
   `conn.execute("BEGIN")` … `conn.commit()` (`migrations.py:145-155`). Python's `sqlite3` in its
   default legacy isolation mode auto-commits DDL in some paths, so a migration whose `ALTER`
   commits while the `schema_version` `INSERT` does not can leave exactly the state above (DDL
   applied, ledger stale). The module docstring already notes "DDL alone autocommits"; the
   wrapping should be re-verified end to end under the connection settings `Store.open` uses
   (`sqlite3.connect(str(path))`, default `isolation_level`, then `apply_pragmas`).

Note: a **fresh** database migrates correctly today — verified headlessly:
`schema_version = (1, 3)`, `entry` has no `logo_png`, `ride` keeps `logo_png`. The failure only
affects pre-existing/inconsistent databases.

## Proposed fix

1. **Make `_migrate_v2_to_v3` idempotent** — drop the column only when it is present:

   ```python
   def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
       columns = {row[1] for row in conn.execute("PRAGMA table_info(entry)")}
       if "logo_png" in columns:
           conn.execute("ALTER TABLE entry DROP COLUMN logo_png")
   ```

   This tolerates both the observed state (ledger `2`, column already gone — the step becomes a
   no-op and the ledger advances to `3`) and databases that predate the column.

2. **Verify and, if needed, harden the ledger/DDL atomicity in `migrate()`** so a migration's DDL
   and its `schema_version` write commit together. Options to weigh: an explicit
   `conn.execute("COMMIT")` to match the explicit `BEGIN`; a `with conn:` block; or setting the
   connection's transaction control explicitly. Decide by test, not by assumption.

3. **Audit the other migrations for the same class of problem.** `_migrate_v1_to_v2`
   (`ALTER TABLE ride ADD COLUMN hold_short_laps …`) will likewise fail if the column already
   exists; `_migrate_v0_to_v1` runs the baseline `CREATE TABLE`s and assumes an empty database.

## Tests to add

- Seed a v2 database whose `entry` table has **no** `logo_png` (the observed state), run
  `migrate()`, assert it reaches version `3`, raises nothing, and `entry` still lacks the column.
- Seed a v2 database **with** `logo_png`, run `migrate()`, assert the column is dropped and the
  ledger reads `3`.
- A property/regression test that replays `MIGRATIONS` from v0 to latest on a fresh file and
  asserts the ledger equals `LATEST_SCHEMA_VERSION` (guards the atomicity failure mode).
- Keep the existing fresh-database coverage in `tests/unit/test_store.py` and
  `tests/property/test_store_replay.py` green.

## Interim mitigation (used)

The affected local database was deleted
(`~/Library/Application Support/RiverCrossing/rides.db` plus its `-wal`/`-shm` sidecars), so the
next launch creates a fresh, correctly-migrated database. This is acceptable only while no real
user data exists; it must not be the shipped remedy.
