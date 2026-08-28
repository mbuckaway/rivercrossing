# SPDX-License-Identifier: GPL-3.0-only
"""Store facade: multi-ride SQLite persistence (spec §2, E5.1.1).

:class:`Store` is the public entry point to the
``rivercrossing.store`` package. It opens one SQLite file per
database, applies the spec §2 PRAGMAs (WAL, synchronous NORMAL,
foreign_keys ON) to every connection, runs the linear migrations, and
exposes the ride surface E5.1.1 owns: :meth:`Store.create_ride` and
:meth:`Store.rides`. The rest of module-skeletons.md S4's surface --
``load_engine``/``append`` (E5.1.2), session bookkeeping (E5.2),
backups (E5.3), ``duplicate_ride``/``delete_ride`` (E5.4), the
``AsyncWriter`` (E5.4), the settings table (E8) -- is deliberately
absent: per the task, nothing is stubbed that nothing calls yet.

Three doc-silence resolutions are recorded here, because the spec
leaves them open and later EPICs will build on them:

- **course_name**: :class:`~rivercrossing.ride.RideConfig` has no
  course field, but spec §2's ``ride`` row has a ``course_name``
  column. Until a setup dialog owns it, ``venue`` doubles for it:
  :meth:`Store.create_ride` writes the config's ``venue`` into both
  columns.
- **rng_seed**: spec §4 makes the seed DB-owned ("Fisher-Yates
  shuffled with the stored ``rng_seed``"; replaying the seed
  reproduces every deal), so :meth:`Store.create_ride` generates a
  fresh seed with ``secrets.randbits`` -- never taken from the config.
- **transaction shape**: the connection runs in sqlite3's default
  (legacy) mode. Each operator action is one committed transaction --
  ``create_ride`` wraps its insert in ``with conn``; ``migrate``
  wraps each migration's DDL plus its version record in an explicit
  BEGIN/COMMIT, because DDL alone autocommits and the two must stay
  atomic (see ``migrations.py``).

:class:`RideRow` is the small frozen summary ``rides()`` returns for
the library -- id, name, event date, status -- with the full library
view-model deferred to E5.4.1.
"""

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

from rivercrossing.ride import RideConfig, RideStatus
from rivercrossing.store.migrations import (
    FutureSchemaVersionError,
    StoreError,
    migrate,
)
from rivercrossing.store.schema import apply_pragmas

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "FutureSchemaVersionError",
    "RideRow",
    "Store",
    "StoreError",
]

_INSERT_RIDE_SQL = """
    INSERT INTO ride (
        name, event_date, venue, course_name, lap_km, organizer, scorer,
        logo_png, planned_start, planned_duration_s, actual_start,
        finished_at, status, entry_mode, max_team_size, plate_model,
        min_lap_s, deck_count, jokers_per_deck, max_cards, tiebreak_order,
        rng_seed, created_at, updated_at
    ) VALUES (
        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
    )
"""


def _to_epoch(value: datetime) -> int:
    """Convert one datetime to UTC epoch seconds.

    A naive datetime is the config's local wall-clock choice (see
    RideConfig's docstring), so it is interpreted as local time before
    conversion; an aware datetime is converted as-is.
    """
    if value.tzinfo is None:
        value = value.astimezone()
    return int(value.timestamp())


@dataclass(frozen=True, slots=True)
class RideRow:
    """One ride's library summary (id, name, date, status).

    The full library view-model arrives with E5.4.1; this is the
    smallest frozen shape ``rides()`` can return today.
    """

    id: int
    name: str
    event_date: date
    status: RideStatus


class Store:
    """Facade over one SQLite database file (WAL, foreign keys ON).

    Create with :meth:`open`, which opens the file (creating it when
    absent), applies the PRAGMAs, runs migrations, and closes the
    connection cleanly on any failure.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        """Wrap an open connection; prefer :meth:`Store.open`."""
        self._conn = conn

    @classmethod
    def open(cls, path: str | Path) -> Store:
        """Open (or create) the database at ``path`` and migrate it.

        Args:
            path: Filesystem path to the SQLite file.

        Returns:
            A ready Store with the connection open until :meth:`close`.

        Raises:
            FutureSchemaVersionError: If the database was written by a
                newer build than this one.
        """
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            apply_pragmas(conn)
            migrate(conn)
        except Exception:
            conn.close()
            raise
        return cls(conn)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    def create_ride(self, config: RideConfig) -> int:
        """Persist one ride from its config; return the new ride id.

        Args:
            config: The ride's setup settings (RideConfig).

        Returns:
            The new ride's id.

        Raises:
            FileNotFoundError: If ``config.logo_path`` names a file
                that does not exist.
        """
        now = int(datetime.now(UTC).timestamp())
        logo_bytes: bytes | None = None
        if config.logo_path is not None:
            logo_bytes = config.logo_path.read_bytes()
        params: tuple[object, ...] = (
            config.name,
            config.event_date.isoformat(),
            config.venue,
            config.venue,  # course_name: venue doubles until a dialog owns it
            config.lap_km,
            config.organizer,
            config.scorer,
            logo_bytes,
            _to_epoch(config.planned_start),
            config.planned_duration_s,
            None,  # actual_start: not started yet
            None,  # finished_at: not finished yet
            RideStatus.DRAFT,
            config.entry_mode.value,
            config.max_team_size,
            config.plate_model.value,
            config.min_lap_s,
            config.deck_count,
            config.jokers_per_deck,
            config.max_cards,
            json.dumps(list(config.tiebreak_order)),
            secrets.randbits(63),  # DB-owned seed (spec §4), never from config
            now,
            now,
        )
        with self._conn:
            cursor = self._conn.execute(_INSERT_RIDE_SQL, params)
        rowid = cursor.lastrowid
        if rowid is None:
            # logic-coverage-exempt: T-3 -- unreachable by construction.
            # A single INSERT on an INTEGER PRIMARY KEY always sets
            # lastrowid; reaching this branch would require mocking
            # sqlite3, which this task's test contract forbids. The
            # guard exists only to narrow typeshed's `int | None`.
            raise RuntimeError("INSERT returned no rowid")
        return rowid

    def rides(self) -> list[RideRow]:
        """Return all ride summaries, oldest first by creation.

        Returns:
            One :class:`RideRow` per ride, ordered by ``created_at``
            (ties broken by id for a stable library listing).
        """
        rows = self._conn.execute(
            "SELECT id, name, event_date, status FROM ride ORDER BY created_at, id"
        ).fetchall()
        return [
            RideRow(
                id=int(row["id"]),
                name=row["name"],
                event_date=date.fromisoformat(row["event_date"]),
                status=RideStatus(row["status"]),
            )
            for row in rows
        ]
