# SPDX-License-Identifier: GPL-3.0-only
"""Store facade: multi-ride SQLite persistence (spec §2, E5.1.1/E5.1.2).

:class:`Store` is the public entry point to the
``rivercrossing.store`` package. It opens one SQLite file per
database, applies the spec §2 PRAGMAs (WAL, synchronous NORMAL,
foreign_keys ON) to every connection, runs the linear migrations, and
exposes the ride surface E5.1.1/E5.1.2 own: :meth:`Store.create_ride`
and :meth:`Store.rides` (E5.1.1) and the event log --
:meth:`Store.append` persists one :class:`~rivercrossing.ride.Event`
as one ``audit`` row and :meth:`Store.load_engine` rebuilds a
:class:`~rivercrossing.ride.RideEngine` by replaying those events
(E5.1.2). The rest of module-skeletons.md S4's surface -- session
bookkeeping (E5.2), backups (E5.3), ``duplicate_ride``/
``delete_ride`` (E5.4), the ``AsyncWriter`` (E5.4), the settings
table (E8) -- is deliberately absent: per the task, nothing is
stubbed that nothing calls yet.

Doc-silence resolutions are recorded here, because the spec leaves
them open and later EPICs will build on them:

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
- **audit at column (E5.1.2)**: ``append`` derives ``audit.at`` from
  the event's own payload timestamp when it carries one
  (``actual_start``/``crossed_at``/``stopped_at``/``finished_at``/
  ``reopened_at``), else from ``now`` -- the audit viewer's "when"
  column (E7.3.1). Replay never reads it: ``load_engine`` orders by
  the insert id, because ``at`` is not monotonic in append order (a
  back-dated ``set_start_time`` sorts earlier than the crossing before
  it), and id order is the only order that preserves the stream.
- **planned_start reconstruction (E5.1.2)**: the ``ride`` row stores
  an epoch, never a tzinfo, so ``load_engine`` reconstructs
  ``planned_start`` as a local naive datetime -- the reverse of
  :func:`_to_epoch`'s naive branch. A naive config value round-trips
  exactly; an aware one keeps its instant but loses its tzinfo (the
  config's own convention is naive local, RideConfig docstring).
- **roster boundary (E5.1.2)**: :meth:`Store.load_engine` takes the
  roster from the caller -- the engine needs plate->entry resolution,
  and full roster-from-DB reconstruction is E5.4.1's job.

:class:`RideRow` is the small frozen summary ``rides()`` returns for
the library -- id, name, event date, status -- with the full library
view-model deferred to E5.4.1.
"""

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, cast

from rivercrossing.cards import Shoe
from rivercrossing.ride import Event, RideConfig, RideEngine, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store.migrations import (
    FutureSchemaVersionError,
    StoreError,
    migrate,
)
from rivercrossing.store.schema import apply_pragmas

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

__all__ = [
    "FutureSchemaVersionError",
    "RideNotFoundError",
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


def _from_epoch(epoch: int) -> datetime:
    """Convert one UTC epoch back to a local naive datetime.

    Reverses :func:`_to_epoch`'s naive branch: ``astimezone()`` then
    ``timestamp()`` on a naive value is the inverse of
    ``datetime.fromtimestamp`` on the same host, so a naive
    ``planned_start`` round-trips exactly (E5.1.2 doc-silence
    resolution in the module docstring). An aware input's instant
    survives; its tzinfo does not -- the epoch column stores only an
    instant, and RideConfig's own convention is naive local.
    """
    return datetime.fromtimestamp(epoch)  # noqa: DTZ006 -- naive local by design, _to_epoch's inverse


# The payload keys whose ISO-8601 values date-stamp an event for the
# audit viewer's ``at`` column (E5.1.2 doc-silence resolution). Events
# carrying none of them -- confirm_held, void_held, deal_manual,
# shoe_reshuffle -- fall back to ``now`` at append time.
_EVENT_TIMESTAMP_KEYS: tuple[str, ...] = (
    "actual_start",
    "crossed_at",
    "stopped_at",
    "finished_at",
    "reopened_at",
)


def _event_timestamp(payload: Mapping[str, object]) -> datetime | None:
    """Return the first ISO-8601 timestamp a payload carries, if any.

    Args:
        payload: An event's payload, exactly as recorded.

    Returns:
        The parsed timestamp of the first recognized key, or None when
        the payload carries none.
    """
    for key in _EVENT_TIMESTAMP_KEYS:
        value = payload.get(key)
        if isinstance(value, str):
            return datetime.fromisoformat(value)
    return None


class RideNotFoundError(StoreError):
    """``Store.load_engine()`` could not find the named ride.

    Raised for a ``ride_id`` no ``ride`` row matches. Defined in the
    package root, unlike the schema-era errors in ``migrations.py``:
    this one has no circular-import pressure, and the facade is its
    only raiser.
    """


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

    # ------------------------------------- E5.1.2 event log (replay)

    def append(self, ride_id: int, event: Event) -> None:
        """Persist one engine event as one audit row in one transaction.

        ``audit.at`` comes from the event's own payload timestamp when
        it carries one, else from ``now`` -- the audit viewer's "when"
        column (E7.3.1), never the replay source: replay reads the
        payload and orders by insert id (module docstring's E5.1.2
        resolution). No return value.

        Args:
            ride_id: The ride the event belongs to.
            event: The event to persist, exactly as the engine recorded
                it.
        """
        stamp = _event_timestamp(event.payload)
        at = _to_epoch(stamp) if stamp is not None else int(datetime.now(UTC).timestamp())
        with self._conn:
            self._conn.execute(
                "INSERT INTO audit (ride_id, at, action, payload_json) VALUES (?, ?, ?, ?)",
                (ride_id, at, event.action, json.dumps(dict(event.payload))),
            )

    def load_engine(
        self,
        ride_id: int,
        roster: Roster,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> RideEngine:
        """Rebuild a RideEngine by replaying one ride's event log.

        Reads the ride row and reconstructs its :class:`RideConfig`
        from the columns ``create_ride`` wrote (``planned_start`` back
        to a local naive datetime, ``tiebreak_order`` from JSON,
        ``logo_path`` to None -- the BLOB is stored, never
        re-materialized as a file), builds a fresh shoe from the stored
        ``rng_seed`` (spec §4: replaying the seed reproduces every
        deal, so no ``Shoe.replay`` is needed), and applies every
        persisted event in append (insert-id) order via
        :meth:`RideEngine.apply`.

        The roster is supplied by the caller -- full roster-from-DB
        reconstruction is E5.4.1's job (module docstring). ``clock``
        defaults to ``datetime.now`` (naive local, matching the naive
        timestamps E4 events carry); tests inject a fake clock for
        determinism.

        Args:
            ride_id: The ride to rebuild.
            roster: This ride's entries/riders, as it existed live.
            clock: Wall-clock source for the rebuilt engine; defaults
                to ``datetime.now``.

        Returns:
            A fresh engine in the ride's live state after replay.

        Raises:
            RideNotFoundError: No ``ride`` row has *ride_id*.
        """
        row = self._conn.execute("SELECT * FROM ride WHERE id = ?", (ride_id,)).fetchone()
        if row is None:
            raise RideNotFoundError(f"no ride with id {ride_id}")
        config = RideConfig(
            name=row["name"],
            event_date=date.fromisoformat(row["event_date"]),
            venue=row["venue"],
            lap_km=row["lap_km"],
            organizer=row["organizer"],
            scorer=row["scorer"],
            planned_start=_from_epoch(row["planned_start"]),
            planned_duration_s=row["planned_duration_s"],
            min_lap_s=row["min_lap_s"],
            entry_mode=EntryMode(row["entry_mode"]),
            plate_model=PlateModel(row["plate_model"]),
            max_team_size=row["max_team_size"],
            deck_count=row["deck_count"],
            jokers_per_deck=row["jokers_per_deck"],
            max_cards=row["max_cards"],
            tiebreak_order=cast("tuple[str, str, str]", tuple(json.loads(row["tiebreak_order"]))),
            logo_path=None,
        )
        engine = RideEngine(
            config=config,
            shoe=Shoe(
                decks=config.deck_count,
                jokers_per_deck=config.jokers_per_deck,
                seed=row["rng_seed"],
            ),
            clock=clock if clock is not None else datetime.now,
            roster=roster,
        )
        events = self._conn.execute(
            "SELECT action, payload_json FROM audit WHERE ride_id = ? ORDER BY id",
            (ride_id,),
        ).fetchall()
        for stored in events:
            engine.apply(
                Event(action=stored["action"], payload=json.loads(stored["payload_json"]))
            )
        return engine
