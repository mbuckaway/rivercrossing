# SPDX-License-Identifier: GPL-3.0-only
"""Linear, numbered, idempotent schema migrations (E5.1.1).

A migration is a function of one connection that moves the schema one
step forward; the tuple :data:`MIGRATIONS` is the whole timeline in
order, so the latest supported version is simply ``len(MIGRATIONS)``.
:func:`migrate` reads the version from the ``schema_version`` ledger,
applies every pending migration in order, each one in its own explicit
transaction (spec §2's one-transaction-per-action
rule -- DDL alone autocommits in sqlite3, so the version record must
share an explicit BEGIN/COMMIT with its DDL to stay atomic), and
records the version it just applied. Re-running on an already-current
database is a no-op, which is what makes re-open idempotent.

The ledger table itself is bootstrapped here (``CREATE TABLE IF NOT
EXISTS``) before the version read, so a v0 database -- empty, no
ledger -- reads as version 0 and upgrades to the latest version
(v1, then v2 as of the W4 short-lap policy, then v3 as of Phase 3's
team-logo-image retirement) on first open.

The store's error types live here, not in the package root, to keep
this module free of a circular import: ``rivercrossing.store`` imports
:func:`migrate`, so ``migrations`` cannot import back from the
package. ``migrations`` is also the first module with a real reason to
raise them. The package root re-exports both names, and that is the
public surface later EPICs import.

A database written by a newer build refuses to open rather than risk a
silent partial read: :func:`migrate` raises
:class:`FutureSchemaVersionError` naming the version it found.
"""

import sqlite3
from typing import TYPE_CHECKING

from rivercrossing.store.schema import SCHEMA_STATEMENTS, SCHEMA_VERSION_DDL

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "LATEST_SCHEMA_VERSION",
    "MIGRATIONS",
    "FutureSchemaVersionError",
    "StoreError",
    "migrate",
]


class StoreError(RuntimeError):
    """A :class:`~rivercrossing.store.Store` operation failed.

    The facade's general error type. Subclasses name the specific
    failure; callers that only care "did it fail" catch this.
    """


class FutureSchemaVersionError(StoreError):
    """The database was written by a newer build than this one.

    Raised on open so a newer-schema file is never half-read: the
    caller gets the version it found and can tell the user to upgrade.
    """


def _migrate_v0_to_v1(conn: sqlite3.Connection) -> None:
    """Create the full spec §2 schema (seven tables)."""
    for statement in SCHEMA_STATEMENTS:
        conn.execute(statement)


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Add the W4 short-lap policy column to the ride table.

    ``hold_short_laps`` is the ride setup dialog's short-lap card
    policy (``RideConfig.hold_short_laps``, default False = always
    deal). The v1 baseline DDL in ``schema.py`` stays untouched --
    this additive ALTER is what brings a shipped v1 database (or a
    fresh file, chained after v0->v1) to v2. ``NOT NULL DEFAULT 0``
    back-fills every existing ride with the always-deal default, so a
    migrated file's replay never fabricates holds it did not record.
    """
    conn.execute("ALTER TABLE ride ADD COLUMN hold_short_laps INTEGER NOT NULL DEFAULT 0")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Drop the retired team logo-image column from the entry table.

    Phase 3 removes the team IMAGE logo: a team's logo is its card
    code alone, so ``entry.logo_png`` has no reader left. The v1
    baseline DDL in ``schema.py`` keeps declaring the column (an
    applied CREATE is immutable -- see that module's docstring), and
    this DROP is what takes it off a shipped v2 database. The ride's
    own ``ride.logo_png`` organisation logo is a different column and
    is deliberately untouched.
    """
    conn.execute("ALTER TABLE entry DROP COLUMN logo_png")


# Migration timeline, oldest first. Append the next migration here and
# LATEST_SCHEMA_VERSION advances by one; never renumber or edit an
# applied migration -- the ledger records what ran.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (
    _migrate_v0_to_v1,
    _migrate_v1_to_v2,
    _migrate_v2_to_v3,
)

LATEST_SCHEMA_VERSION: int = len(MIGRATIONS)


def _current_version(conn: sqlite3.Connection) -> int:
    """Return the ledger's version, or 0 when none is recorded."""
    row = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()
    return int(row["version"]) if row is not None else 0


def migrate(conn: sqlite3.Connection) -> None:
    """Bring one connection's schema up to LATEST_SCHEMA_VERSION.

    Creates the ledger if absent, refuses databases from newer builds,
    then applies each pending migration in one transaction, recording
    the version after each one. Safe to re-run: an already-current
    database changes nothing.

    Args:
        conn: The connection to migrate. Expects the schema PRAGMAs
            already applied (see
            :func:`rivercrossing.store.schema.apply_pragmas`).

    Raises:
        FutureSchemaVersionError: If the database's schema_version is
            higher than this build supports.
    """
    conn.execute(SCHEMA_VERSION_DDL)
    current = _current_version(conn)
    if current > LATEST_SCHEMA_VERSION:
        raise FutureSchemaVersionError(
            f"Database schema version {current} is newer than this build "
            f"supports ({LATEST_SCHEMA_VERSION}). "
            "Upgrade the app before opening this database."
        )
    for target in range(current + 1, LATEST_SCHEMA_VERSION + 1):
        conn.execute("BEGIN")
        try:
            MIGRATIONS[target - 1](conn)
            conn.execute(
                "INSERT OR REPLACE INTO schema_version (id, version) VALUES (1, ?)",
                (target,),
            )
        except sqlite3.Error:
            conn.rollback()
            raise
        conn.commit()
