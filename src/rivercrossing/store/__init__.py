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
(E5.1.2). E5.2.1 adds the session bookkeeping: :meth:`Store.open`
records one ``app_session`` row per launch (``active_ride_id`` when a
ride is running), :meth:`Store.close_session` stamps ``closed_at`` on
a clean quit, and :meth:`Store.session_state` reads the previous
session's record -- clean quit vs crash vs running-at-exit -- for the
R-52 resume dialog. E5.3.1 adds the backup mechanism
(``rivercrossing.store.backup``, module-skeletons.md S4):
:meth:`Store.delete_ride` (E5.3.2) calls ``backup.run`` before it
deletes, so R-18's "automatic database backup is written first" is
backup-then-delete inside the facade. The rest of S4's surface --
``duplicate_ride`` (E5.4), the ``AsyncWriter`` (E5.4) -- is
deliberately absent: per the task, nothing is stubbed that nothing
calls yet. (Settings persistence is not a table here: E8.1.1 stores
per-user settings in the JSON config file under
``rivercrossing.ui.presenters.settings``.)

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
- **roster boundary (E5.1.2)**: :meth:`Store.load_engine` took the
  roster from the caller -- the engine needs plate->entry resolution,
  and full roster-from-DB reconstruction was E5.4.1's job.
- **roster persistence (E5.4.1)**: :meth:`Store.save_roster` writes
  every entry and rider into spec §2's tables (replacing any prior
  snapshot, one transaction); :meth:`Store.roster_for` and
  :meth:`Store.load_engine` rebuild the full roster from those rows.
  ``has_data`` is derived, never stored: the schema has no column for
  it, and R-15's permanent delete guard is exactly "has recorded
  data", so ``_load_roster`` marks an entry ``has_data`` when it owns
  a crossing or card row. ``entry.status`` persists; ``entry.dnf_at``
  stays NULL and the rider's ``emergency_contact``/``waiver_signed``/
  ``ccn_reg_id`` stay NULL -- the in-memory Roster model carries no
  such fields, so there is nothing honest to store.
- **duplicate name (E5.4.1)**: :meth:`Store.duplicate_ride` names the
  copy ``f"{source name} (copy)"`` by default (the retired 3d mock
  drew a "New ride name" input, but the E5.4.1 confirm dialog is
  mock-first simple -- ``message_lbl`` + stock buttons, no input --
  so the Store derives the name; a caller may pass ``name=``).
- **duplication audit (E5.4.1)**: :meth:`Store.duplicate_ride` audits
  nothing. The audit table is the replay channel
  (:meth:`Store.load_engine` applies every row), and
  ``RideEngine.apply`` raises on an unknown action -- a
  ``duplicate_ride`` row would break replay of the copy. A separate,
  non-replayed audit channel is E7's audit-viewer work, not this
  task's.
- **fresh-DB session_state (E5.2.1)**: :meth:`Store.session_state`
  with no prior session row returns ``SessionState.CLEAN_QUIT`` -- a
  first launch is not a crash and has no ride to resume (the
  reasonable default, pinned in the method docstring and tests).
- **previous session (E5.2.1)**: :meth:`Store.session_state` reads the
  second-newest ``app_session`` row -- the session the current open's
  row supersedes -- never the newest open row, whose ``closed_at`` is
  NULL by construction and would otherwise always read as a crash.
- **resume reading (E5.2.2)**: :meth:`Store.previous_session` is the
  richer reading R-52's dialog needs -- state, the running ride's id
  and the copy's time -- and :meth:`Store.session_state` is its thin
  state-only projection, so the two can never drift. :meth:`Store.
  set_active_ride` records the running ride on the *current* open row
  (the resume dialog only learns the ride after :meth:`Store.open`
  inserted the session); a clean quit then stamps ``closed_at`` on a
  row that carries the ride.
- **crash copy time (E5.2.2)**: spec §3 words a crash "closed
  unexpectedly at 12:41 (last heartbeat)". The 30 s session heartbeat
  task spec §10 names does not exist yet, so ``heartbeat_at`` is NULL
  in every store this task can build; :meth:`Store.previous_session`
  falls back to ``opened_at`` -- the last instant the crashed session
  is known to have been alive -- and E5.3+ replaces the fallback with
  real heartbeat writes.
- **roster shell (E5.2.2)**: :meth:`Store.roster_for` returned an
  *empty* :class:`~rivercrossing.roster.Roster` carrying the ride's
  own entry_mode/plate_model/max_team_size until E5.4.1 -- no Store
  method wrote entry/rider rows yet, so an empty roster was the only
  honest reconstruction. E5.4.1 replaces it with the full
  roster-from-DB rebuild (same method, complete result).
- **backup location / WAL / rotation (E5.3.1)**: backups live in a
  sibling ``<db>.backups/`` directory as one fixed-width, sortable,
  UTC-timestamped ``<stem>.<YYYYmmdd-HHMMSS-ffffff>.db`` file each;
  a live WAL store is checkpointed (``PRAGMA wal_checkpoint(TRUNCATE)``)
  through a short-lived second connection before the copy; rotation
  prunes to the newest ``keep`` (R-54's 20) by filename sort. Full
  detail, plus the hourly seam and restore, is recorded in
  ``backup.py``'s own module docstring -- the one place the rule
  lives.
- **delete order (E5.3.2)**: :meth:`Store.delete_ride` validates
  first (unknown id, typed-name mismatch, RUNNING refusal), then
  calls ``backup.run`` -- R-18's backup-first -- and only then
  deletes, in one transaction, in FK-safe order. The schema declares
  plain ``REFERENCES`` with **no ON DELETE CASCADE** (spec §2's own
  DDL), so the facade removes the dependents itself: cards, then
  crossings, then riders, then entries, then audit rows, then NULLs
  ``app_session.active_ride_id``, then the ride row.
- **delete guards read the stored row (E5.3.2)**: the RUNNING refusal
  reads the ``ride`` table's ``status`` column -- the persisted truth
  the library shows. ``create_ride`` writes ``draft`` and today the
  engine's ``start`` event lands in the audit log without the facade
  syncing the row (E5.4's engine-sync writes it), so the delete guard
  is correct against the stored value.
- **audit_rows projection (E7.3.1)**: :meth:`Store.audit_rows` returns
  the UI's own :class:`~rivercrossing.ui.presenters.data_source.
  AuditRow` view-model rather than a second, store-side row type --
  E7.3.1's "reuse the AuditRow projection" ruling, and the one place a
  core module imports the wx-free ``rivercrossing.ui.presenters``
  package (the same seam ``rivercrossing.demo`` already implements).
  ``when`` derives from the stored ``at`` epoch (the audit viewer's
  When column, spec §13's stored-UTC/displayed-local), never re-parsed
  from the payload.
- **E5.3.2/E5.4.1 UI boundary**: ``RideLibrary`` gets its R-18
  enablement and its Delete-button -> ``delete_ride_dlg`` wiring, and
  ``app.py`` threads a store-backed ``on_delete`` callback when a
  store is open. With no store open the callback is ``None`` and the
  no-store library shows the E5.4.2 empty state, so nothing matches a
  store ride and Delete is a no-op; the store-backed library E5.4.1
  wires is where the callback matches real rows.

:class:`RideRow` is the small frozen summary ``rides()`` returns for
the library -- id, name, event date, status -- with the full library
view-model deferred to E5.4.1.
"""

import json
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, cast

from rivercrossing.cards import Shoe
from rivercrossing.ride import Event, RideConfig, RideEngine, RideStatus
from rivercrossing.roster import (
    Entry,
    EntryMode,
    EntryStatus,
    EntryType,
    PlateModel,
    Rider,
    Roster,
)
from rivercrossing.store.backup import run as _backup_run
from rivercrossing.store.migrations import (
    FutureSchemaVersionError,
    StoreError,
    migrate,
)
from rivercrossing.store.schema import apply_pragmas
from rivercrossing.ui.presenters.data_source import AuditRow

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = [
    "FutureSchemaVersionError",
    "PreviousSession",
    "RideNameMismatchError",
    "RideNotFoundError",
    "RideRow",
    "RideRunningError",
    "SessionState",
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

_INSERT_SESSION_SQL = """
    INSERT INTO app_session (opened_at, closed_at, active_ride_id, heartbeat_at)
    VALUES (?, NULL, ?, NULL)
"""


def _insert_session(conn: sqlite3.Connection, active_ride_id: int | None) -> None:
    """Record the new session row on an opened connection (E5.2.1).

    ``opened_at`` is now epoch; ``closed_at`` stays NULL so a clean
    quit (:meth:`Store.close_session`) is distinguishable from a crash
    (:meth:`Store.session_state`). ``active_ride_id`` references the
    ``ride`` table, so an unknown id fails the foreign key.
    """
    now = int(datetime.now(UTC).timestamp())
    with conn:
        conn.execute(_INSERT_SESSION_SQL, (now, active_ride_id))


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


def _audit_when(epoch: int) -> str:
    """Render an ``audit.at`` epoch as the viewer's local ``HH:MM:SS``.

    ``append`` stores ``at`` as a UTC epoch; spec §13's "stored UTC,
    displayed local" means the audit viewer's When column (E7.3.1)
    renders it in local wall time -- the same rule
    ``data_source._feed_time`` applies to the engine-derived
    projection.
    """
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S")  # noqa: DTZ006 -- local display, _to_epoch's inverse


class RideNotFoundError(StoreError):
    """``Store.load_engine()`` could not find the named ride.

    Raised for a ``ride_id`` no ``ride`` row matches. Defined in the
    package root, unlike the schema-era errors in ``migrations.py``:
    this one has no circular-import pressure, and the facade is its
    only raiser.
    """


class RideNameMismatchError(StoreError):
    """``Store.delete_ride()`` was given the wrong typed name (R-18).

    Raised when *typed_name* does not byte-for-byte equal the ride's
    stored name -- no case-fold, no ``.strip()``, per UX-DESKTOP §4's
    type-to-confirm rule. Raised before any backup or delete runs.
    """


class RideRunningError(StoreError):
    """``Store.delete_ride()`` refused a RUNNING ride (R-18, spec §3).

    Raised when the ride's stored status is ``running`` -- a running
    ride is never deletable. Raised before any backup or delete runs.
    """


class SessionState(Enum):
    """The previous app session's state, for the R-52 resume reading.

    ``session_state()`` returns one of these on launch, before the
    resume dialog words its copy (E5.2.2):

    - ``CLEAN_QUIT``: the previous session recorded ``closed_at`` and
      no running ride -- a normal quit.
    - ``CRASHED``: the previous session never wrote ``closed_at`` --
      the app died without quitting.
    - ``RUNNING_AT_EXIT``: the previous session closed cleanly while a
      ride was running (``closed_at`` present and ``active_ride_id``
      set) -- quitting keeps the ride running on wall time.
    """

    CLEAN_QUIT = "clean_quit"
    CRASHED = "crashed"
    RUNNING_AT_EXIT = "running_at_exit"


@dataclass(frozen=True, slots=True)
class PreviousSession:
    """The previous app session's record, for the E5.2.2 resume reading.

    ``session_state()`` answers only *whether* the previous session
    was a clean quit, a crash or a quit-keep-running; the resume
    dialog also needs *which* ride was running and *when* the session
    ended, so :meth:`Store.previous_session` returns all three as one
    frozen record (R-52).

    Attributes:
        state: How the previous session ended (a :class:`SessionState`).
        ride_id: The ride that was running at that session's end
            (``app_session.active_ride_id``); ``None`` when no ride
            was running.
        ended_at: The instant the dialog's copy shows: ``closed_at``
            for a clean quit, else the last ``heartbeat_at`` -- today
            ``opened_at``, since no heartbeat task writes yet (module
            docstring's E5.2.2 resolution). ``None`` only on a fresh
            database with no prior session.
    """

    state: SessionState
    ride_id: int | None
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class RideRow:
    """One ride's library summary (id, name, date, status, entries).

    E5.4.1 fills in the last field: ``entries`` is the cheap COUNT the
    library's Entries column draws (schema has it; xrc-windows.md D's
    rides_list shows Ride | Date | Status | Entries). Demo-era
    constructions that name only the first four fields keep working
    through the zero default.
    """

    id: int
    name: str
    event_date: date
    status: RideStatus
    entries: int = 0


class Store:
    """Facade over one SQLite database file (WAL, foreign keys ON).

    Create with :meth:`open`, which opens the file (creating it when
    absent), applies the PRAGMAs, runs migrations, and closes the
    connection cleanly on any failure.
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        *,
        path: str | Path | None = None,
    ) -> None:
        """Wrap an open connection; prefer :meth:`Store.open`.

        ``path`` is the backing file :meth:`Store.open` opened; only
        :meth:`Store.open` sets it, and :meth:`delete_ride` needs it
        to write the pre-delete backup. A Store constructed directly
        (tests only) has no path and refuses to delete.
        """
        self._conn = conn
        self._path = Path(path) if path is not None else None

    @classmethod
    def open(cls, path: str | Path, *, active_ride_id: int | None = None) -> Store:
        """Open (or create) the database at ``path`` and migrate it.

        After migrations, records the new app session (E5.2.1): one
        ``app_session`` row with ``opened_at`` = now epoch and
        ``closed_at`` NULL (the clean-quit vs crash signal R-52 words
        the resume dialog from), plus ``active_ride_id`` when a ride is
        running at launch -- the row :meth:`close_session` stamps on a
        clean quit.

        Args:
            path: Filesystem path to the SQLite file.
            active_ride_id: The running ride's id, when the app opens
                onto a ride that is RUNNING; ``None`` otherwise.

        Returns:
            A ready Store with the connection open until :meth:`close`.

        Raises:
            FutureSchemaVersionError: If the database was written by a
                newer build than this one.
            sqlite3.IntegrityError: If *active_ride_id* names no ride
                row (the ``REFERENCES ride(id)`` foreign key).
        """
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            apply_pragmas(conn)
            migrate(conn)
            _insert_session(conn, active_ride_id)
        except Exception:
            conn.close()
            raise
        return cls(conn, path=path)

    def close(self) -> None:
        """Close the underlying connection."""
        self._conn.close()

    # ------------------------------------- E5.2.1 session bookkeeping

    def close_session(self) -> None:
        """Write ``closed_at`` on the open session (a clean quit).

        Stamps the newest ``app_session`` row -- the one :meth:`open`
        inserted -- with now epoch. Called by the exit flow when the
        user confirms quitting; a session left un-stamped is how
        :meth:`session_state` recognises a crash (R-52). With no
        session row at all this is a no-op (the table is empty, so no
        row updates).
        """
        now = int(datetime.now(UTC).timestamp())
        with self._conn:
            self._conn.execute(
                "UPDATE app_session SET closed_at = ?"
                " WHERE id = (SELECT id FROM app_session ORDER BY id DESC LIMIT 1)",
                (now,),
            )

    def _previous_session_row(self) -> sqlite3.Row | None:
        """Return the previous ``app_session`` row, or None if none.

        The one row-reading both :meth:`session_state` and
        :meth:`previous_session` use: the second-newest row -- the
        session the current :meth:`open` call's row supersedes, never
        the current open row (whose ``closed_at`` is NULL by
        construction, and whose ``active_ride_id`` the resume flow may
        not even have set yet).
        """
        return cast(
            "sqlite3.Row | None",
            self._conn.execute(
                "SELECT closed_at, active_ride_id, heartbeat_at, opened_at"
                " FROM app_session ORDER BY id DESC LIMIT 1 OFFSET 1"
            ).fetchone(),
        )

    def session_state(self) -> SessionState:
        """Return the PREVIOUS session's state (launch reading, R-52).

        Reads the second-newest ``app_session`` row -- the session the
        current :meth:`open` call's row supersedes, never the current
        open row (whose ``closed_at`` is NULL by construction). The
        reading:

        - ``closed_at`` NULL -> :attr:`SessionState.CRASHED`.
        - ``closed_at`` present and ``active_ride_id`` set ->
          :attr:`SessionState.RUNNING_AT_EXIT` (quit kept a running
          ride on the wall clock).
        - ``closed_at`` present, no running ride ->
          :attr:`SessionState.CLEAN_QUIT`.
        - no prior session (a fresh database) ->
          :attr:`SessionState.CLEAN_QUIT` -- a first launch is not a
          crash, and there is no ride to resume (doc-silence
          resolution: the reasonable default).

        Returns:
            The previous session's :class:`SessionState`.
        """
        return self.previous_session().state

    def previous_session(self) -> PreviousSession:
        """Return the PREVIOUS session's full resume record (E5.2.2).

        The R-52 reading behind :meth:`session_state`, carrying the
        three fields the resume dialog actually words its copy from:
        the state (same closed_at/active_ride_id logic), the running
        ride's id, and the instant the copy's time shows --
        ``closed_at`` for a clean quit, else the last ``heartbeat_at``,
        falling back to ``opened_at`` while no heartbeat task writes
        (module docstring's E5.2.2 resolution).

        Returns:
            The previous session's :class:`PreviousSession`.
        """
        row = self._previous_session_row()
        if row is None:
            return PreviousSession(SessionState.CLEAN_QUIT, None, None)
        if row["closed_at"] is None:
            state = SessionState.CRASHED
            ended_epoch = (
                row["heartbeat_at"] if row["heartbeat_at"] is not None else row["opened_at"]
            )
        elif row["active_ride_id"] is not None:
            state = SessionState.RUNNING_AT_EXIT
            ended_epoch = row["closed_at"]
        else:
            state = SessionState.CLEAN_QUIT
            ended_epoch = row["closed_at"]
        return PreviousSession(
            state=state,
            ride_id=row["active_ride_id"],
            ended_at=_from_epoch(ended_epoch),
        )

    def set_active_ride(self, ride_id: int) -> None:
        """Record the running ride on the OPEN session (E5.2.2, R-52).

        :meth:`open` inserts the launch session before the bootstrap
        knows which ride -- if any -- the resume dialog chose to
        continue, so Continue marks that open row here: a later clean
        quit (:meth:`close_session`) then stamps ``closed_at`` on a
        session that carries the ride, and a crash reads
        CRASHED-with-a-ride. With no session row at all this is a
        no-op (nothing updates).
        """
        with self._conn:
            self._conn.execute(
                "UPDATE app_session SET active_ride_id = ?"
                " WHERE id = (SELECT id FROM app_session ORDER BY id DESC LIMIT 1)",
                (ride_id,),
            )

    def roster_for(self, ride_id: int) -> Roster:
        """Return *ride_id*'s full roster reconstructed from the DB.

        E5.4.1 closes E5.1.2's roster boundary (module docstring): this
        rebuilds the complete roster -- entries and riders in creation
        order, with entry notes and ``has_data`` derived from recorded
        rows -- instead of the empty shell E5.2.2 had to settle for.
        :meth:`load_engine` calls the same reconstruction when no
        caller roster is supplied.

        Args:
            ride_id: The ride whose roster to rebuild.

        Returns:
            A fresh :class:`~rivercrossing.roster.Roster` carrying the
            ride's own entry_mode/plate_model/max_team_size and every
            persisted entry and rider.

        Raises:
            RideNotFoundError: No ``ride`` row has *ride_id*.
        """
        return self._load_roster(ride_id)

    def _load_roster(self, ride_id: int) -> Roster:
        """Rebuild *ride_id*'s roster from the entry/rider tables.

        The one reading both :meth:`roster_for` and :meth:`load_engine`
        use: the ride row's shape columns build the shell, then every
        entry (creation order) and its riders (``sort_order``, id tie-
        break for a stable order) reconstruct the field. ``has_data``
        is derived, never stored (module docstring's E5.4.1
        resolution): an entry that owns a crossing or card row has
        recorded data.

        Raises:
            RideNotFoundError: No ``ride`` row has *ride_id*.
        """
        row = self._conn.execute(
            "SELECT entry_mode, plate_model, max_team_size FROM ride WHERE id = ?",
            (ride_id,),
        ).fetchone()
        if row is None:
            raise RideNotFoundError(f"no ride with id {ride_id}")
        roster = Roster(
            entry_mode=EntryMode(row["entry_mode"]),
            plate_model=PlateModel(row["plate_model"]),
            max_team_size=row["max_team_size"],
        )
        entries: list[Entry] = []
        for entry_row in self._conn.execute(
            "SELECT id, plate, display_name, type, status, notes"
            " FROM entry WHERE ride_id = ? ORDER BY id",
            (ride_id,),
        ).fetchall():
            riders = [
                Rider(
                    name=rider_row["name"],
                    plate=rider_row["plate"],
                    sort_order=rider_row["sort_order"],
                )
                for rider_row in self._conn.execute(
                    "SELECT name, plate, sort_order FROM rider"
                    " WHERE entry_id = ? ORDER BY sort_order, id",
                    (entry_row["id"],),
                ).fetchall()
            ]
            has_data = bool(
                self._conn.execute(
                    "SELECT EXISTS(SELECT 1 FROM crossing WHERE entry_id = ?)"
                    " OR EXISTS(SELECT 1 FROM card WHERE entry_id = ?)",
                    (entry_row["id"], entry_row["id"]),
                ).fetchone()[0]
            )
            entry = Entry(
                plate=entry_row["plate"],
                display_name=entry_row["display_name"],
                type=EntryType(entry_row["type"]),
                riders=riders,
                status=EntryStatus(entry_row["status"]),
                notes=entry_row["notes"] or "",
            )
            entry.has_data = has_data
            entries.append(entry)
        roster.load_entries(entries)
        return roster

    def save_roster(self, ride_id: int, roster: Roster) -> None:
        """Persist one ride's roster, replacing any previously saved.

        E5.4.1's roster persistence into spec §2's entry/rider tables:
        every entry (plate, display_name, type, team_size, status,
        notes) and every rider (name, plate, sort_order) is written in
        one transaction, after removing any previously saved rows -- a
        save is a snapshot of the live roster, never an append (the
        replace semantics the rider editor's DRAFT edits need).
        ``has_data`` is deliberately not stored (derived at load time
        from recorded rows), and ``dnf_at``/``emergency_contact``/
        ``waiver_signed``/``ccn_reg_id`` stay NULL -- the in-memory
        Roster model carries no such fields (module docstring's E5.4.1
        resolutions).

        Args:
            ride_id: The ride whose roster to write.
            roster: The roster to persist.

        Raises:
            RideNotFoundError: No ``ride`` row has *ride_id*.
        """
        row = self._conn.execute("SELECT id FROM ride WHERE id = ?", (ride_id,)).fetchone()
        if row is None:
            raise RideNotFoundError(f"no ride with id {ride_id}")
        with self._conn:
            self._conn.execute(
                "DELETE FROM rider WHERE entry_id IN (SELECT id FROM entry WHERE ride_id = ?)",
                (ride_id,),
            )
            self._conn.execute("DELETE FROM entry WHERE ride_id = ?", (ride_id,))
            for entry in roster.entries:
                cursor = self._conn.execute(
                    "INSERT INTO entry"
                    " (ride_id, plate, display_name, type, team_size, status, dnf_at, notes)"
                    " VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                    (
                        ride_id,
                        entry.plate,
                        entry.display_name,
                        entry.type.value,
                        len(entry.riders),
                        entry.status.value,
                        entry.notes,
                    ),
                )
                entry_id = cursor.lastrowid
                if entry_id is None:
                    # logic-coverage-exempt: T-3 -- unreachable by
                    # construction, the same lastrowid narrowing as
                    # create_ride: a single INSERT on an INTEGER PRIMARY
                    # KEY always sets it, and this task's test contract
                    # forbids mocking sqlite3.
                    raise RuntimeError("INSERT returned no rowid")
                for rider in entry.riders:
                    self._conn.execute(
                        "INSERT INTO rider"
                        " (entry_id, name, plate, sort_order, emergency_contact,"
                        " waiver_signed, ccn_reg_id)"
                        " VALUES (?, ?, ?, ?, NULL, NULL, NULL)",
                        (entry_id, rider.name, rider.plate, rider.sort_order),
                    )

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

        Each row carries the ride's entry count (the library's Entries
        column) via a correlated subquery -- cheap, and the schema has
        the data.

        Returns:
            One :class:`RideRow` per ride, ordered by ``created_at``
            (ties broken by id for a stable library listing).
        """
        rows = self._conn.execute(
            "SELECT r.id, r.name, r.event_date, r.status,"
            " (SELECT COUNT(*) FROM entry e WHERE e.ride_id = r.id) AS entries"
            " FROM ride r ORDER BY r.created_at, r.id"
        ).fetchall()
        return [
            RideRow(
                id=int(row["id"]),
                name=row["name"],
                event_date=date.fromisoformat(row["event_date"]),
                status=RideStatus(row["status"]),
                entries=int(row["entries"]),
            )
            for row in rows
        ]

    # -------------------------------- E5.3.2 delete guard

    def delete_ride(self, ride_id: int, typed_name: str) -> None:
        """Delete one ride and all its data (R-18, spec §3).

        Exactly R-18's order: refuses unless *typed_name* byte-for-byte
        equals the ride's stored name (no case-fold, no ``.strip()`` --
        UX-DESKTOP §4), refuses a RUNNING ride outright, writes an
        automatic backup FIRST (``backup.run``), and only then deletes.
        Because the schema declares plain ``REFERENCES`` with no ON
        DELETE CASCADE (module docstring's E5.3.2 resolution), the
        dependents are removed explicitly, in FK-safe order, in one
        transaction: cards, crossings, riders, entries, audit rows;
        ``app_session.active_ride_id`` is NULLed; then the ride row.

        Args:
            ride_id: The ride to delete.
            typed_name: The name the operator typed into
                ``delete_ride_dlg``'s ``confirm_name_input``.

        Raises:
            RideNotFoundError: No ``ride`` row has *ride_id*.
            RideNameMismatchError: *typed_name* does not equal the
                ride's name.
            RideRunningError: The ride's stored status is RUNNING.
            StoreError: The store has no backing path (constructed
                directly, not via :meth:`Store.open`).
        """
        row = self._conn.execute(
            "SELECT name, status FROM ride WHERE id = ?", (ride_id,)
        ).fetchone()
        if row is None:
            raise RideNotFoundError(f"no ride with id {ride_id}")
        if typed_name != row["name"]:
            raise RideNameMismatchError(
                f"typed name {typed_name!r} does not match ride {ride_id} name {row['name']!r}"
            )
        if row["status"] == RideStatus.RUNNING:
            raise RideRunningError(f"ride {ride_id} is RUNNING and cannot be deleted")
        if self._path is None:
            # logic-coverage-exempt: T-3 -- unreachable through any live
            # construction. Every Store is built by Store.open, which
            # always sets _path; reaching this guard requires calling
            # __init__ directly, which this task's test contract does
            # not do. The guard narrows _path to a real Path for mypy.
            raise StoreError("store has no backing path; construct via Store.open")
        _backup_run(self._path)
        with self._conn:
            self._conn.execute("DELETE FROM card WHERE ride_id = ?", (ride_id,))
            self._conn.execute("DELETE FROM crossing WHERE ride_id = ?", (ride_id,))
            self._conn.execute(
                "DELETE FROM rider WHERE entry_id IN (SELECT id FROM entry WHERE ride_id = ?)",
                (ride_id,),
            )
            self._conn.execute("DELETE FROM entry WHERE ride_id = ?", (ride_id,))
            self._conn.execute("DELETE FROM audit WHERE ride_id = ?", (ride_id,))
            self._conn.execute(
                "UPDATE app_session SET active_ride_id = NULL WHERE active_ride_id = ?",
                (ride_id,),
            )
            self._conn.execute("DELETE FROM ride WHERE id = ?", (ride_id,))

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
        roster: Roster | None = None,
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

        The roster is rebuilt from the DB by default (E5.4.1 closes
        E5.1.2's caller-supplied boundary); a caller may still pass
        ``roster=`` to override -- the only other caller is
        ``app.py``'s resume flow, which passes the same roster
        :meth:`roster_for` returned. ``clock`` defaults to
        ``datetime.now`` (naive local, matching the naive timestamps
        E4 events carry); tests inject a fake clock for determinism.

        Args:
            ride_id: The ride to rebuild.
            roster: This ride's entries/riders; ``None`` reconstructs
                them from the entry/rider tables.
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
            roster=roster if roster is not None else self._load_roster(ride_id),
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

    # ------------------------- E7.3.1 audit viewer read accessor

    def audit_rows(self, ride_id: int) -> list[AuditRow]:
        """Return one ride's audit trail rows, newest first (E7.3.1).

        The audit viewer's read accessor: every ``audit`` row the
        ride recorded, projected to the display
        :class:`~rivercrossing.ui.presenters.data_source.AuditRow`
        shape the viewer's list draws -- ``who="scorer"`` (the engine
        never records another actor), ``entry`` = the payload's
        ``entry_id`` (falling back to ``plate``, then ``""``),
        ``reason`` = the payload's ``reason``, and ``when`` rendered
        from the stored ``at`` epoch as local ``HH:MM:SS`` (spec §13:
        stored UTC, displayed local). Newest first by insert id -- the
        same order the viewer draws -- never by ``at``, which is not
        monotonic in append order (module docstring's E5.1.2
        resolution).

        Args:
            ride_id: The ride whose audit trail to read.

        Returns:
            One :class:`AuditRow` per recorded event, newest first;
            ``[]`` for a ride with no events.

        Raises:
            RideNotFoundError: No ``ride`` row has *ride_id*.
        """
        row = self._conn.execute("SELECT id FROM ride WHERE id = ?", (ride_id,)).fetchone()
        if row is None:
            raise RideNotFoundError(f"no ride with id {ride_id}")
        stored = self._conn.execute(
            "SELECT at, action, payload_json FROM audit WHERE ride_id = ? ORDER BY id DESC",
            (ride_id,),
        ).fetchall()
        rows: list[AuditRow] = []
        for audit_row in stored:
            payload = json.loads(audit_row["payload_json"])
            rows.append(
                AuditRow(
                    when=_audit_when(audit_row["at"]),
                    who="scorer",
                    action=audit_row["action"],
                    entry=str(payload.get("entry_id") or payload.get("plate") or ""),
                    reason=str(payload.get("reason") or ""),
                )
            )
        return rows

    # ------------------------------------- E5.4.1 duplicate_ride (R-15)

    def duplicate_ride(self, ride_id: int, *, name: str | None = None) -> int:
        """Copy one ride's setup + roster to a new DRAFT ride (R-15).

        R-15's "setup + roster, no timing data": reads the source ride
        row, inserts a new ride row copying every config column with a
        fresh DB-owned ``rng_seed`` (spec §4 -- a new ride gets its own
        seed, never the source's), status DRAFT and NULL
        ``actual_start``/``finished_at``, then copies the roster rows
        (entries + riders, in creation order). No crossings, cards or
        audit rows are written -- the copy has no timing data by
        construction. The duplication itself is not audited either
        (module docstring's E5.4.1 decision): the audit replay channel
        only knows ride mutations, so a ``duplicate_ride`` row would
        break :meth:`load_engine`.

        Args:
            ride_id: The source ride.
            name: The copy's name; default ``f"{source name} (copy)"``
                (module docstring's E5.4.1 resolution).

        Returns:
            The new ride's id.

        Raises:
            RideNotFoundError: No ``ride`` row has *ride_id*.
        """
        row = self._conn.execute("SELECT * FROM ride WHERE id = ?", (ride_id,)).fetchone()
        if row is None:
            raise RideNotFoundError(f"no ride with id {ride_id}")
        now = int(datetime.now(UTC).timestamp())
        params: tuple[object, ...] = (
            name if name is not None else f"{row['name']} (copy)",
            row["event_date"],
            row["venue"],
            row["course_name"],
            row["lap_km"],
            row["organizer"],
            row["scorer"],
            row["logo_png"],
            row["planned_start"],
            row["planned_duration_s"],
            None,  # actual_start: the copy has never started
            None,  # finished_at
            RideStatus.DRAFT,
            row["entry_mode"],
            row["max_team_size"],
            row["plate_model"],
            row["min_lap_s"],
            row["deck_count"],
            row["jokers_per_deck"],
            row["max_cards"],
            row["tiebreak_order"],
            secrets.randbits(63),  # fresh seed (spec §4)
            now,
            now,
        )
        with self._conn:
            cursor = self._conn.execute(_INSERT_RIDE_SQL, params)
            new_id = cursor.lastrowid
            if new_id is None:
                # logic-coverage-exempt: T-3 -- unreachable by
                # construction, the same lastrowid narrowing as
                # create_ride (module docstring's delete-order note).
                raise RuntimeError("INSERT returned no rowid")
            for source_entry in self._conn.execute(
                "SELECT id, plate, display_name, type, team_size, status, notes"
                " FROM entry WHERE ride_id = ? ORDER BY id",
                (ride_id,),
            ).fetchall():
                entry_cursor = self._conn.execute(
                    "INSERT INTO entry"
                    " (ride_id, plate, display_name, type, team_size, status, dnf_at, notes)"
                    " VALUES (?, ?, ?, ?, ?, ?, NULL, ?)",
                    (
                        new_id,
                        source_entry["plate"],
                        source_entry["display_name"],
                        source_entry["type"],
                        source_entry["team_size"],
                        source_entry["status"],
                        source_entry["notes"],
                    ),
                )
                new_entry_id = entry_cursor.lastrowid
                if new_entry_id is None:
                    # logic-coverage-exempt: T-3 -- unreachable by
                    # construction (lastrowid narrowing, as above).
                    raise RuntimeError("INSERT returned no rowid")
                for rider in self._conn.execute(
                    "SELECT name, plate, sort_order FROM rider"
                    " WHERE entry_id = ? ORDER BY sort_order, id",
                    (source_entry["id"],),
                ).fetchall():
                    self._conn.execute(
                        "INSERT INTO rider"
                        " (entry_id, name, plate, sort_order, emergency_contact,"
                        " waiver_signed, ccn_reg_id)"
                        " VALUES (?, ?, ?, ?, NULL, NULL, NULL)",
                        (new_entry_id, rider["name"], rider["plate"], rider["sort_order"]),
                    )
        return new_id
