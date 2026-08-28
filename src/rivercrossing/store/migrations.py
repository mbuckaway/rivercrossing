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
ledger -- reads as version 0 and upgrades to v1 on first open.

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


# Migration timeline, oldest first. Append the next migration here and
# LATEST_SCHEMA_VERSION advances by one; never renumber or edit an
# applied migration -- the ledger records what ran.
MIGRATIONS: tuple[Callable[[sqlite3.Connection], None], ...] = (_migrate_v0_to_v1,)

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
