# SPDX-License-Identifier: GPL-3.0-only
"""SQLite DDL and per-connection PRAGMAs (spec §2, E5.1.1).

The seven tables below are spec §2's own ``ride``, ``entry``,
``rider``, ``crossing``, ``card``, ``app_session`` and ``audit``,
column for column, plus one addition of our own: the
:data:`SCHEMA_VERSION_DDL` ledger (a single integer row holding the
migration version) that ``rivercrossing.store.migrations`` reads and
records. The skeleton's ``settings`` table is deliberately absent:
spec.md §2 does not list one, and E8.1.1 stores per-user settings in
a JSON config file (``rivercrossing.ui.presenters.settings``), not
this database -- the schema stays untouched.

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
  (``rider_pooled``/``team_relay``), ``entry.type``/``entry.status``,
  ``crossing.flag``, ``card.rank`` (0 = joker, else 2-14),
  ``card.suit`` and ``card.state``.

PRAGMAs are applied per connection by :func:`apply_pragmas`:
``journal_mode=WAL`` and ``synchronous=NORMAL`` for R-50's
at-most-one-keystroke crash window, ``foreign_keys=ON`` for the
``REFERENCES`` clauses (SQLite defaults it off). ``foreign_keys`` and
``synchronous`` are per-connection settings, so every open must re-run
them; ``journal_mode`` persists in the file once set.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

__all__ = [
    "PRAGMA_STATEMENTS",
    "SCHEMA_STATEMENTS",
    "SCHEMA_VERSION_DDL",
    "apply_pragmas",
]

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
        max_cards          INTEGER,
        tiebreak_order     TEXT    NOT NULL,
        rng_seed           INTEGER NOT NULL,
        created_at         INTEGER NOT NULL,
        updated_at         INTEGER NOT NULL
    )
    """,
    """
    CREATE TABLE entry (
        id           INTEGER PRIMARY KEY,
        ride_id      INTEGER NOT NULL REFERENCES ride(id),
        plate        TEXT    NOT NULL,
        display_name TEXT    NOT NULL,
        type         TEXT    NOT NULL CHECK (type IN ('solo', 'team')),
        team_size    INTEGER NOT NULL,
        status       TEXT    NOT NULL CHECK (status IN ('active', 'dnf')),
        dnf_at       INTEGER,
        notes        TEXT,
        UNIQUE (ride_id, plate)
    )
    """,
    """
    CREATE TABLE rider (
        id                INTEGER PRIMARY KEY,
        entry_id          INTEGER NOT NULL REFERENCES entry(id),
        name              TEXT    NOT NULL,
        plate             TEXT,
        sort_order        INTEGER NOT NULL,
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
