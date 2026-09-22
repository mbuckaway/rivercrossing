# SPDX-License-Identifier: GPL-3.0-only
"""SQLite DDL, version gate and PRAGMAs (spec §2, E5.1.1).

The seven tables below are spec §2's own ``ride``, ``entry``,
``rider``, ``crossing``, ``card``, ``app_session`` and ``audit``,
column for column, plus one addition of our own: the
:data:`SCHEMA_VERSION_DDL` ledger (a single integer row holding the
schema version) that :func:`ensure_schema` reads and records. The
skeleton's ``settings`` table is deliberately absent: spec.md §2 does
not list one, and E8.1.1 stores per-user settings in a JSON config
file (``rivercrossing.ui.presenters.settings``), not this database --
the schema stays untouched.

**Versioned and migrated in place.** :data:`SCHEMA_VERSION` is 3 and
the DDL below is the *latest* shape, not a frozen baseline. The
product-owner policy (spec §2): every schema change bumps
:data:`SCHEMA_VERSION` and ships the step that upgrades an older file,
in :mod:`rivercrossing.store.migrations`. :func:`ensure_schema` creates
this schema on an empty file (ledger version 0), runs the chain on an
older one, does nothing on a current one, and refuses a file stamped
*newer* than this build -- there is no downgrade path.

Version 1 was the released flattened baseline, and the changes under
**Version 1** below were folded into its CREATE while the project was
unreleased. **Version 2** is the pooled-live-move seam, and the
v1 -> v2 migration rebuilds ``entry`` for it, because SQLite cannot
drop a table-level ``UNIQUE``. **Version 3** changes no DDL at all: it
is a data-only step that rewrites the ``audit`` payloads a pre-v2
build wrote -- the plated ``entry_id``/``old_entry_id``/``new_entry_id``
the replay seam now resolves as keys -- which is why the DDL below is
still v2's shape.

**Version 2** adds, both in the table the migration rebuilds:

- ``entry.key`` -- the entry's stable identity (E3.1.2's
  pooled-live-move seam), a surrogate the ride engine files crossings
  and credited hands under where a derived plate is mutable -- is a
  real column, NOT NULL, right after the ``plate`` it is the stable
  counterpart of.
- ``entry.retired`` -- 0 for a live entry, 1 for one a pooled move
  dissolved after it had recorded data (the same seam's persistence
  half) -- is a real column with a NOT NULL DEFAULT 0, and the
  per-ride plate uniqueness moved off the table into a partial unique
  index over the live rows alone (``entry_plate_live_unique``,
  ``WHERE retired = 0``): spec §2's "plate UNIQUE per ride" still
  holds for the field, while a solo->team move may leave the retired
  solo row holding the very plate the destination team has since
  adopted (S1's lowest-numbered-rider derivation).

**Version 1** carries:

- ``ride.hold_short_laps`` -- the short-lap card policy -- is a real
  column (NOT NULL, DEFAULT 1 = hold short-lap cards for review), in
  the same position ``_INSERT_RIDE_SQL`` lists it.
- ``ride.jokers_mode`` -- the shoe's jokers mode (Phase 5: ``per_deck``
  re-deals ``jokers_per_deck`` jokers every cycle, ``total`` spends
  them once per ride) -- is a real column too, NOT NULL DEFAULT
  ``'total'``, in the same position ``_INSERT_RIDE_SQL`` lists it.
- ``entry.logo_png`` is gone: a team's logo is its ``logo_card`` code
  alone. The ride's own ``ride.logo_png`` organisation logo is a
  different column and stays.
- ``rider.sex`` carries the rider's ``"M"``/``"F"``, NULL when the
  sex is unknown.

Two schema decisions are recorded here because the spec is silent:

- ``event_date`` is stored as ISO-8601 ``TEXT`` (``YYYY-MM-DD``), not
  a UTC epoch. Spec §2's "Timestamps are UTC epoch" governs instants
  (``planned_start``, ``actual_start``, ``created_at``); a calendar
  date has no instant to convert, and the ISO form round-trips
  :class:`~rivercrossing.ride.RideConfig.event_date` exactly.
- Every enumerated column gets a CHECK over spec §2's own closed
  list, so a wrong stored spelling can never land in the file:
  ``ride.status`` (the four :class:`RideStatus` spellings),
  ``ride.entry_mode`` (``solo``/``mixed``), ``ride.plate_model``
  (``rider_pooled``/``team_relay``), ``ride.jokers_mode``
  (``per_deck``/``total``), ``entry.type``/``entry.status``,
  ``crossing.flag``, ``card.rank`` (0 = joker, else 2-14),
  ``card.suit`` and ``card.state``.

PRAGMAs are applied per connection by :func:`apply_pragmas`:
``journal_mode=WAL`` and ``synchronous=NORMAL`` for R-50's
at-most-one-keystroke crash window, ``foreign_keys=ON`` for the
``REFERENCES`` clauses (SQLite defaults it off). ``foreign_keys`` and
``synchronous`` are per-connection settings, so every open must re-run
them; ``journal_mode`` persists in the file once set.
"""

import sqlite3

from rivercrossing.store.migrations import run_migrations

__all__ = [
    "PRAGMA_STATEMENTS",
    "SCHEMA_STATEMENTS",
    "SCHEMA_VERSION",
    "SCHEMA_VERSION_DDL",
    "SchemaVersionMismatchError",
    "StoreError",
    "apply_pragmas",
    "ensure_schema",
]

# The schema version this build reads and writes. A file stamped with
# an older one is upgraded in place by ``store.migrations``; a file
# stamped with a newer one is refused by :func:`ensure_schema` rather
# than read under a shape it was not written with.
SCHEMA_VERSION = 3


class StoreError(RuntimeError):
    """A :class:`~rivercrossing.store.Store` operation failed.

    The facade's general error type. Subclasses name the specific
    failure; callers that only care "did it fail" catch this.

    Defined here, beside the schema that raises it, rather than in the
    package root: ``rivercrossing.store`` imports this module, so a
    package-root definition would be a circular import. The package
    root re-exports the name, and that is the public surface callers
    import.
    """


class SchemaVersionMismatchError(StoreError):
    """The database's stamped schema version is not this build's.

    Raised on open so a file from a different build is never half-read:
    the caller gets the version it found, the version this build
    expects, and the way out (rename or delete the file).
    """


# Per spec §2, applied to every connection the Store opens.
PRAGMA_STATEMENTS: tuple[str, ...] = (
    "PRAGMA journal_mode=WAL",
    "PRAGMA synchronous=NORMAL",
    "PRAGMA foreign_keys=ON",
)

# The migration ledger: one row, id pinned to 1 by the CHECK, holding
# the current schema version. INSERT OR REPLACE keeps it at one row.
SCHEMA_VERSION_DDL = (
    "CREATE TABLE IF NOT EXISTS schema_version ("
    "id INTEGER PRIMARY KEY CHECK (id = 1), "
    "version INTEGER NOT NULL)"
)

SCHEMA_STATEMENTS: tuple[str, ...] = (
    """
    CREATE TABLE ride (
        id                 INTEGER PRIMARY KEY,
        name               TEXT    NOT NULL,
        event_date         TEXT    NOT NULL,
        venue              TEXT    NOT NULL,
        course_name        TEXT    NOT NULL,
        lap_km             REAL    NOT NULL,
        organizer          TEXT    NOT NULL,
        scorer             TEXT    NOT NULL,
        logo_png           BLOB,
        planned_start      INTEGER NOT NULL,
        planned_duration_s INTEGER NOT NULL,
        actual_start       INTEGER,
        finished_at        INTEGER,
        status             TEXT    NOT NULL DEFAULT 'draft'
                             CHECK (status IN
                               ('draft', 'running', 'finished', 'reopened')),
        entry_mode         TEXT    NOT NULL
                             CHECK (entry_mode IN ('solo', 'mixed')),
        max_team_size      INTEGER NOT NULL,
        plate_model        TEXT    NOT NULL
                             CHECK (plate_model IN
                               ('rider_pooled', 'team_relay')),
        min_lap_s          INTEGER NOT NULL,
        deck_count         INTEGER NOT NULL,
        jokers_per_deck    INTEGER NOT NULL,
        jokers_mode        TEXT    NOT NULL DEFAULT 'total'
                             CHECK (jokers_mode IN ('per_deck', 'total')),
        max_cards          INTEGER,
        tiebreak_order     TEXT    NOT NULL,
        rng_seed           INTEGER NOT NULL,
        created_at         INTEGER NOT NULL,
        updated_at         INTEGER NOT NULL,
        hold_short_laps    INTEGER NOT NULL DEFAULT 1
    )
    """,
    """
    CREATE TABLE entry (
        id           INTEGER PRIMARY KEY,
        ride_id      INTEGER NOT NULL REFERENCES ride(id),
        plate        TEXT    NOT NULL,
        key          TEXT    NOT NULL,
        display_name TEXT    NOT NULL,
        type         TEXT    NOT NULL CHECK (type IN ('solo', 'team')),
        team_size    INTEGER NOT NULL,
        status       TEXT    NOT NULL CHECK (status IN ('active', 'dnf')),
        dnf_at       INTEGER,
        notes        TEXT,
        logo_card    TEXT,
        retired      INTEGER NOT NULL DEFAULT 0
    )
    """,
    # Spec §2's "plate UNIQUE per ride", enforced for the live field
    # alone: a retired entry keeps the plate it held (a solo->team move
    # re-derives the destination team's plate to exactly that number).
    """
    CREATE UNIQUE INDEX entry_plate_live_unique
        ON entry (ride_id, plate) WHERE retired = 0
    """,
    """
    CREATE TABLE rider (
        id                INTEGER PRIMARY KEY,
        entry_id          INTEGER NOT NULL REFERENCES entry(id),
        first_name        TEXT    NOT NULL,
        last_name         TEXT    NOT NULL,
        plate             TEXT,
        sort_order        INTEGER NOT NULL,
        sex               TEXT    CHECK (sex IN ('M', 'F')),
        emergency_contact TEXT,
        waiver_signed     INTEGER,
        ccn_reg_id        TEXT
    )
    """,
    """
    CREATE TABLE crossing (
        id          INTEGER PRIMARY KEY,
        ride_id     INTEGER NOT NULL REFERENCES ride(id),
        entry_id    INTEGER NOT NULL REFERENCES entry(id),
        rider_id    INTEGER REFERENCES rider(id),
        seq         INTEGER NOT NULL,
        crossed_at  INTEGER NOT NULL,
        lap_s       INTEGER NOT NULL,
        flag        TEXT    NOT NULL
                      CHECK (flag IN ('none', 'short', 'manual')),
        voided      INTEGER NOT NULL DEFAULT 0,
        void_reason TEXT,
        UNIQUE (entry_id, seq)
    )
    """,
    """
    CREATE TABLE card (
        id          INTEGER PRIMARY KEY,
        ride_id     INTEGER NOT NULL REFERENCES ride(id),
        entry_id    INTEGER NOT NULL REFERENCES entry(id),
        crossing_id INTEGER REFERENCES crossing(id),
        shoe_index  INTEGER,
        rank        INTEGER NOT NULL
                      CHECK (rank = 0 OR rank BETWEEN 2 AND 14),
        suit        TEXT    CHECK (suit IN ('s', 'h', 'd', 'c')),
        state       TEXT    NOT NULL
                      CHECK (state IN ('held', 'dealt', 'voided')),
        dealt_at    INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE app_session (
        id             INTEGER PRIMARY KEY,
        opened_at      INTEGER NOT NULL,
        closed_at      INTEGER,
        active_ride_id INTEGER REFERENCES ride(id),
        heartbeat_at   INTEGER
    )
    """,
    """
    CREATE TABLE audit (
        id           INTEGER PRIMARY KEY,
        ride_id      INTEGER NOT NULL REFERENCES ride(id),
        at           INTEGER NOT NULL,
        action       TEXT    NOT NULL,
        payload_json TEXT    NOT NULL
    )
    """,
)


def apply_pragmas(conn: sqlite3.Connection) -> None:
    """Apply the spec §2 PRAGMAs to one connection.

    Call once per opened connection, before any transaction starts:
    ``PRAGMA foreign_keys=ON`` cannot change inside a transaction.
    """
    for statement in PRAGMA_STATEMENTS:
        conn.execute(statement)


def _current_version(conn: sqlite3.Connection) -> int:
    """Return the ledger's stamped version, or 0 when absent."""
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    return int(row["version"]) if row is not None else 0


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Bring the database to :data:`SCHEMA_VERSION` on one connection.

    The ledger table is bootstrapped first (CREATE IF NOT EXISTS, so
    re-running is harmless), then the stamped version decides:

    - ``0`` -- no ledger row yet: create the current schema and stamp
      :data:`SCHEMA_VERSION`, all inside one explicit BEGIN/COMMIT so
      a mid-create failure rolls the whole schema back rather than
      leaving a half-built database behind (DDL alone autocommits in
      sqlite3, so the version record must share the transaction).
    - :data:`SCHEMA_VERSION` -- already current: a no-op, which is what
      makes re-open idempotent.
    - older -- upgrade in place: run
      :func:`~rivercrossing.store.migrations.run_migrations` from the
      stamped version to :data:`SCHEMA_VERSION`, then stamp it. Every
      schema change ships the step that upgrades a file written before
      it (product-owner policy, spec §2), so a file is never refused
      for being old.
    - newer -- a file from a later build: refuse, naming the version
      found and the one expected. There is no downgrade path.

    Args:
        conn: The connection to bring to the current schema. Expects
            the schema PRAGMAs already applied (see
            :func:`apply_pragmas`).

    Raises:
        SchemaVersionMismatchError: If the database's stamped version
            is newer than :data:`SCHEMA_VERSION`.
    """
    conn.execute(SCHEMA_VERSION_DDL)
    current = _current_version(conn)
    if current == SCHEMA_VERSION:
        return
    if current == 0:
        _create_schema(conn)
        return
    if current > SCHEMA_VERSION:
        raise SchemaVersionMismatchError(
            f"Database schema version {current} does not match this build's "
            f"schema version {SCHEMA_VERSION}. "
            "Rename or delete the database file to continue."
        )
    run_migrations(conn, current, SCHEMA_VERSION)


def _create_schema(conn: sqlite3.Connection) -> None:
    """Create the current schema and stamp it, in one transaction."""
    conn.execute("BEGIN")
    try:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)",
            (SCHEMA_VERSION,),
        )
    except sqlite3.Error:
        conn.rollback()
        raise
    conn.commit()
