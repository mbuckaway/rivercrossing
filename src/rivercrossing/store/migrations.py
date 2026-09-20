# SPDX-License-Identifier: GPL-3.0-only
"""Versioned schema migrations (spec §2).

One step per schema version: :data:`MIGRATIONS` maps the version a file
is stamped with to the function that upgrades it to the next one, so
``MIGRATIONS[1]`` takes a v1 file to v2. :func:`run_migrations` walks
that chain in order and stamps the ledger;
:func:`~rivercrossing.store.schema.ensure_schema` calls it whenever it
opens a file older than the build, because the product-owner policy is
that every schema change ships the step that upgrades older files
rather than refusing them.

A step is **frozen history**: it describes one jump between two
versions once, for good. It is never edited to match a later DDL --
``schema.py``'s ``SCHEMA_STATEMENTS`` is the latest shape and moves on
without it, while the file on disk still holds exactly what the step
expects. The ``MIGRATIONS``-covers-every-older-version test in
``tests/unit/test_store.py`` is what keeps the two ends honest.
"""

import sqlite3
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["MIGRATIONS", "run_migrations"]

# The v2 ``entry`` table, frozen at the version this step targets: v1's
# table plus ``key`` (the entry's stable identity, E3.1.2's
# pooled-live-move seam) and ``retired`` (1 once a pooled move has
# dissolved an entry that recorded data), and without v1's inline
# ``UNIQUE (ride_id, plate)`` -- SQLite cannot drop a table-level
# constraint, which is why the step below is a full table rebuild.
_ENTRY_V2_DDL = """
CREATE TABLE entry_new (
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
"""

# Spec §2's "plate UNIQUE per ride" for the live field alone: a retired
# entry keeps the plate it held, which the destination team of a
# solo->team move may have since adopted (S1's lowest-numbered-rider
# derivation).
_ENTRY_PLATE_LIVE_INDEX_DDL = (
    "CREATE UNIQUE INDEX entry_plate_live_unique ON entry (ride_id, plate) WHERE retired = 0"
)

_V1_ENTRY_SELECT_SQL = (
    "SELECT id, ride_id, plate, display_name, type, team_size, status,"
    " dnf_at, notes, logo_card FROM entry ORDER BY id"
)

_ENTRY_V2_INSERT_SQL = (
    "INSERT INTO entry_new"
    " (id, ride_id, plate, key, display_name, type, team_size, status,"
    " dnf_at, notes, logo_card, retired)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)"
)


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Rebuild ``entry`` in the v2 shape, minting one fresh key per row.

    SQLite cannot drop v1's inline ``UNIQUE (ride_id, plate)``, so this
    is the documented table-rebuild procedure: create the v2 table under
    a temporary name, copy every row into it, drop v1's table and rename
    the new one into place.

    ``PRAGMA foreign_keys=OFF`` runs before ``BEGIN`` because the pragma
    is silently ignored inside a transaction: with enforcement on,
    ``DROP TABLE entry`` would clear the child tables' rows and the
    rename could rewrite their ``REFERENCES`` clauses. The
    ``PRAGMA foreign_key_check`` before the COMMIT is the procedure's
    own safety net -- it reports a link the rebuild broke, and any the
    file already carried, so the migration aborts and rolls back instead
    of shipping a database whose laps point at nothing.

    Each row keeps its ``id``: ``rider``, ``crossing`` and ``card``
    reference it, and a fresh id would orphan every recorded lap. Each
    row's ``key`` is drawn here as ``uuid.uuid4().hex`` -- the exact
    expression ``roster.Entry.key`` fills its ``default_factory`` from
    -- so a migrated key is indistinguishable from one the roster would
    have minted itself: 32 lowercase hex digits, version nibble 4.
    ``retired`` is 0, because v1 had no retired entries and nothing in a
    v1 file could say otherwise.

    Args:
        conn: An open connection to a file stamped version 1.

    Raises:
        sqlite3.IntegrityError: If ``PRAGMA foreign_key_check`` reports
            a row after the rebuild. The transaction is rolled back and
            the connection's ``foreign_keys`` setting restored, so the
            caller is left with the untouched v1 file.
    """
    conn.execute("PRAGMA foreign_keys=OFF")
    try:
        conn.execute("BEGIN")
        try:
            conn.execute(_ENTRY_V2_DDL)
            for (
                entry_id,
                ride_id,
                plate,
                display_name,
                entry_type,
                team_size,
                status,
                dnf_at,
                notes,
                logo_card,
            ) in conn.execute(_V1_ENTRY_SELECT_SQL):
                conn.execute(
                    _ENTRY_V2_INSERT_SQL,
                    (
                        entry_id,
                        ride_id,
                        plate,
                        uuid.uuid4().hex,
                        display_name,
                        entry_type,
                        team_size,
                        status,
                        dnf_at,
                        notes,
                        logo_card,
                    ),
                )
            conn.execute("DROP TABLE entry")
            conn.execute("ALTER TABLE entry_new RENAME TO entry")
            conn.execute(_ENTRY_PLATE_LIVE_INDEX_DDL)
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise sqlite3.IntegrityError(
                    "v1 -> v2 schema migration left foreign key violations in "
                    f"{', '.join(sorted({str(row[0]) for row in violations}))}"
                )
        except sqlite3.Error:
            conn.rollback()
            raise
        conn.commit()
    finally:
        conn.execute("PRAGMA foreign_keys=ON")


# The chain: source version -> the step that upgrades it to source + 1.
MIGRATIONS: dict[int, Callable[[sqlite3.Connection], None]] = {1: _migrate_v1_to_v2}


def run_migrations(conn: sqlite3.Connection, from_version: int, to_version: int) -> None:
    """Upgrade a database from *from_version* to *to_version*, in order.

    Each step runs in its own transaction and the ledger is stamped
    once, after the last of them has committed, so the version on
    record only ever names a chain that finished.

    Args:
        conn: An open connection to a file stamped *from_version*.
        from_version: The version the file's ledger currently holds.
        to_version: The version to reach -- this build's
            ``SCHEMA_VERSION``.

    Raises:
        KeyError: If *from_version* names no registered step. Version 0
            is an empty file, which ``ensure_schema`` creates rather
            than migrates.
    """
    for version in range(from_version, to_version):
        MIGRATIONS[version](conn)
    conn.execute(
        "INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)",
        (to_version,),
    )
    conn.commit()
