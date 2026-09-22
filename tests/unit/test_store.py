# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.store (E5.1.1: schema + version gate).

Tests first (R-70). The store's schema is versioned and migrated:
``Store.open`` creates the current schema on a fresh file and stamps
``SCHEMA_VERSION``, re-opening a current file is an idempotent no-op,
a file stamped OLDER is upgraded in place by the migration chain, and
only a file stamped NEWER than this build refuses to open, with
:class:`~rivercrossing.store.SchemaVersionMismatchError` naming what
it found (product-owner policy: every schema change bumps the version
and ships a migration, spec §2).

E5.1.1's own surface is small: ``create_ride`` persists a
:class:`RideConfig` row (logo BLOB, JSON tiebreak order, DB-owned
seed) and ``rides()`` lists the library.

The last section covers the pooled-live-move persistence seam: an
entry a move dissolved after it recorded data is written with
``entry.retired = 1`` and reloads into the roster's retired
collection, which is what keeps its stable key resolvable for a
replayed ``record_crossing``. The per-ride plate uniqueness moved to
a partial unique index over the live rows alone, so a retired row may
hold the plate the destination team has since adopted.

No mocks anywhere: every test drives real sqlite3 against a
``tmp_path`` file (the task's own "no mocks of sqlite3 beyond
tmp_path DB files" rule). Assertions that inspect stored columns read
the file back through a second, independent connection -- the point is
what landed on disk, not what the facade keeps in memory. The one
seam two tests fake is ``secrets.randbits`` -- the OS CSPRNG
``create_ride`` draws a missing seed from (T-10's I/O boundary) --
where a recording stand-in pins the draw's width and count.
"""

import base64
import json
import re
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from platformdirs import user_data_dir

import rivercrossing.store as store_module
from conftest import entry_key
from rivercrossing.cards import Card, Shoe, ShoeEmpty
from rivercrossing.ride import (
    DEFAULT_DECK_COUNT,
    DEFAULT_JOKERS_PER_DECK,
    JOKERS_MODE_PER_DECK,
    JOKERS_MODE_TOTAL,
    Event,
    RideConfig,
    RideStatus,
)
from rivercrossing.roster import Entry, EntryMode, PlateModel, Rider, Roster
from rivercrossing.store import (
    RideNameMismatchError,
    RideNotFoundError,
    RideRow,
    RideRunningError,
    SchemaVersionMismatchError,
    SessionState,
    Store,
    StoreError,
    backup,
)
from rivercrossing.store.migrations import MIGRATIONS, run_migrations
from rivercrossing.store.schema import (
    SCHEMA_STATEMENTS,
    SCHEMA_VERSION,
    SCHEMA_VERSION_DDL,
)
from rivercrossing.ui.presenters.data_source import AuditRow

# The same always-valid kwarg set test_ride.py builds from, so a
# store test probes one field at a time without second-guessing the
# others (T-8's one-focused-assertion spirit, applied to arrange too).
_VALID_KWARGS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "event_date": date(2026, 9, 20),
    "venue": "Sea to Sky Gondola",
    "lap_km": 8.0,
    "organizer": "GORBA",
    "scorer": "K. Singh",
    # naive, by design: planned_start is a pre-persistence, local
    # wall-clock value (RideConfig's own docstring) -- UTC-epoch
    # conversion is the Store's concern.
    "planned_start": datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001
    "planned_duration_s": 21600,
    "min_lap_s": 1080,
    "entry_mode": EntryMode.MIXED,
    "plate_model": PlateModel.RIDER_POOLED,
}

# A canonical 1x1 transparent PNG (67 bytes) -- a real, readable image
# file for the logo BLOB round-trip, not a placeholder byte string.
_TINY_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)


def _config(**overrides: object) -> RideConfig:
    """Build a valid RideConfig, overriding only what a test names."""
    return RideConfig(**{**_VALID_KWARGS, **overrides})  # type: ignore[arg-type]


def _fetch_ride_row(path: Path, ride_id: int) -> dict[str, object]:
    """Read one stored ride row back out of the file (assertion aid)."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM ride WHERE id = ?", (ride_id,)).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AssertionError(f"no ride row with id {ride_id}")
    return dict(row)


def _deal_all(shoe: Shoe) -> list[Card]:
    """Deal every remaining card of *shoe*'s current cycle, in order."""
    dealt: list[Card] = []
    while True:
        try:
            card, _ = shoe.deal()
        except ShoeEmpty:
            return dealt
        dealt.append(card)


# ------------------------------------------------------------- open


def test_store_open_fresh_db_creates_the_full_current_schema(tmp_path: Path) -> None:
    """A fresh file gets every spec table at the current shape.

    The current (v2) shape: ``ride.hold_short_laps`` and
    ``ride.jokers_mode`` are plain columns, ``entry`` carries the stable
    ``key`` and the ``retired`` flag, ``rider`` carries the nullable
    ``sex``, and no retired ``logo_png`` image column is anywhere.
    """
    db_path = tmp_path / "rides.db"

    store = Store.open(db_path)
    store.close()

    expected = {
        "ride",
        "entry",
        "rider",
        "crossing",
        "card",
        "app_session",
        "audit",
        "schema_version",
    }
    with closing(sqlite3.connect(str(db_path))) as conn:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        ride_columns = {row[1] for row in conn.execute("PRAGMA table_info(ride)")}
        entry_columns = {row[1] for row in conn.execute("PRAGMA table_info(entry)")}
        rider_columns = {row[1] for row in conn.execute("PRAGMA table_info(rider)")}
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    assert expected <= names
    assert {"hold_short_laps", "jokers_mode"} <= ride_columns
    assert {"key", "retired", "logo_card"} <= entry_columns
    assert "logo_png" not in entry_columns
    assert "sex" in rider_columns


def test_store_open_creates_missing_parent_directories(tmp_path: Path) -> None:
    """A db path whose parent dirs do not exist yet opens fine.

    E9.1 (the store-backed bootstrap): the app's first launch on a
    clean machine opens ``user_data_dir()/rides.db``, and that
    directory does not exist until the app creates it -- sqlite3 alone
    would raise ``unable to open database file`` and the frozen binary
    would crash at launch (measured on clean CI images).
    """
    db_path = tmp_path / "app" / "data" / "nested" / "rides.db"

    store = Store.open(db_path)
    store.close()

    assert db_path.is_file()
    with closing(sqlite3.connect(str(db_path))) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_store_open_stamps_the_ledger_at_the_current_schema_version(tmp_path: Path) -> None:
    """One ledger row, stamped at the current schema version."""
    db_path = tmp_path / "rides.db"

    Store.open(db_path).close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        version = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]

    assert SCHEMA_VERSION == 3
    assert (version, rows) == (SCHEMA_VERSION, 1)


def test_store_open_applies_spec_pragmas_to_every_connection(tmp_path: Path) -> None:
    """WAL + NORMAL + foreign_keys ON, per spec §2, per connection."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        # Per-connection PRAGMAs are observable only on the store's own
        # connection: synchronous NORMAL is 1, foreign_keys ON is 1.
        assert store._conn.execute("PRAGMA synchronous").fetchone()[0] == 1
        assert store._conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        # Behaviorally: a row referencing a missing ride is rejected.
        with pytest.raises(sqlite3.IntegrityError, match=re.escape("FOREIGN KEY")):
            store._conn.execute(
                "INSERT INTO entry"
                " (ride_id, plate, key, display_name, type, team_size, status)"
                " VALUES (999, 'P1', '2f7a', 'ghost', 'solo', 1, 'active')"
            )
    finally:
        store.close()
    # WAL is a persistent file property, so a later connection sees it.
    with closing(sqlite3.connect(str(db_path))) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_store_open_current_version_database_reopens_as_a_noop_keeping_its_rides(
    tmp_path: Path,
) -> None:
    """Reopening a current file keeps its rides untouched."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    ride_id = store.create_ride(_config(name="Idempotent"))
    store.close()

    reopened = Store.open(db_path)
    try:
        rides = reopened.rides()
    finally:
        reopened.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        version = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        rows = conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]

    assert rides == [
        RideRow(
            id=ride_id,
            name="Idempotent",
            event_date=date(2026, 9, 20),
            status=RideStatus.DRAFT,
        )
    ]
    assert (version, rows) == (SCHEMA_VERSION, 1)


def test_store_open_given_an_empty_file_creates_the_full_current_schema(tmp_path: Path) -> None:
    """A pre-schema file becomes a full current-version database."""
    db_path = tmp_path / "v0.db"
    sqlite3.connect(str(db_path)).close()

    store = Store.open(db_path)
    try:
        rides = store.rides()
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version = conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
        columns = {row[1] for row in conn.execute("PRAGMA table_info(ride)")}

    assert rides == []
    assert {"ride", "entry", "rider", "crossing", "card", "app_session", "audit"} <= names
    assert version == SCHEMA_VERSION
    assert "hold_short_laps" in columns


@pytest.mark.parametrize("stored_version", [4, 99, 999])
def test_store_open_given_a_newer_version_raises_naming_it(
    tmp_path: Path, stored_version: int
) -> None:
    """A file stamped above SCHEMA_VERSION refuses to open, naming it.

    Older stamps migrate (there is no reason to refuse a file this build
    can upgrade); a NEWER one has no downgrade path, so it is refused
    rather than read under a shape it was not written with. The refusal
    names what the ledger holds, what this build expects, and the way
    out (rename or delete the file), and it is a :class:`StoreError`
    subclass so existing "did it fail" callers keep working.
    """
    db_path = tmp_path / f"v{stored_version}.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(SCHEMA_VERSION_DDL)
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, ?)", (stored_version,))
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(
        SchemaVersionMismatchError,
        match=re.escape(
            f"Database schema version {stored_version} does not match this build's "
            f"schema version {SCHEMA_VERSION}. "
            "Rename or delete the database file to continue."
        ),
    ):
        Store.open(db_path)


def test_store_open_given_a_mid_create_failure_rolls_back_to_an_empty_ledger(
    tmp_path: Path,
) -> None:
    """A failure while creating v1 leaves no half-built schema behind.

    The ledger table is bootstrapped first (mirroring the old migrate
    flow), then every CREATE runs inside one explicit BEGIN; a
    collision rolls the whole transaction back, so the database has an
    empty ledger and none of the partially-created tables.
    """
    db_path = tmp_path / "conflict.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # A pre-schema table colliding with the last CREATE statement.
        conn.execute("CREATE TABLE audit (x INTEGER)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(sqlite3.OperationalError, match=re.escape("already exists")):
        Store.open(db_path)

    with closing(sqlite3.connect(str(db_path))) as check:
        names = {
            row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        version_rows = check.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0]

    assert "ride" not in names  # earlier DDL was rolled back, not half-created
    assert "audit" in names  # the pre-schema conflicting table survived
    assert "schema_version" in names  # ledger bootstrapped, no version row
    assert version_rows == 0


def test_store_open_retries_transient_wal_io_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A transient WAL I/O error after a hard kill retries, not fatal.

    Windows-measured: reopening a DB whose writer was
    TerminateProcess'd can hit a one-shot ``disk I/O error`` on the
    first PRAGMA while the -wal lock settles (R-52's crash-recovery
    path must not fail on a transient). The fault is injected at the
    store's own PRAGMA seam -- sqlite3 itself stays real, per this
    file's discipline -- because a transient OS error cannot be
    produced deterministically with real I/O.
    """
    real_apply = store_module.apply_pragmas
    calls = {"n": 0}

    def flaky_apply(conn: sqlite3.Connection) -> None:
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("disk I/O error")
        real_apply(conn)

    monkeypatch.setattr(store_module, "apply_pragmas", flaky_apply)
    db_path = tmp_path / "store.db"

    store = store_module.Store.open(db_path)
    store.close()

    assert calls["n"] >= 2


def test_store_open_does_not_retry_persistent_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persistent failure surfaces immediately, no futile retries.

    The retry is bounded to transient conditions; a schema collision
    ("already exists") must raise on the first attempt exactly as
    before (the mid-create rollback test).
    """
    calls = {"n": 0}

    def failing_apply(_conn: sqlite3.Connection) -> None:
        calls["n"] += 1
        raise sqlite3.OperationalError("table ride already exists")

    monkeypatch.setattr(store_module, "apply_pragmas", failing_apply)

    with pytest.raises(sqlite3.OperationalError, match=re.escape("already exists")):
        store_module.Store.open(tmp_path / "store.db")

    assert calls["n"] == 1


def test_store_schema_version_mismatch_error_is_a_store_error() -> None:
    """The refusal surfaces as a StoreError subclass."""
    assert issubclass(SchemaVersionMismatchError, StoreError)


# ------------------------------------------------ v1 -> v2 migration
# Product-owner policy (spec §2): every schema change bumps
# SCHEMA_VERSION and ships a migration that upgrades an older file in
# place. These tests hand-build a released-build v1 database -- the
# frozen DDL below, never SCHEMA_STATEMENTS, because what is actually on
# disk in a 1.0.x file is the whole point -- and drive it through
# Store.open.
#
# v1 -> v2 touches ``entry`` alone: it gains the stable ``key`` and the
# ``retired`` flag, and its inline ``UNIQUE (ride_id, plate)`` moves to
# the partial unique index over the live rows (SQLite cannot drop a
# table-level UNIQUE, so the migration is the standard table rebuild).
# Every other table is frozen here unchanged, so a later edit to
# SCHEMA_STATEMENTS can never rewrite what an existing file holds.

_V1_LEDGER_DDL = (
    "CREATE TABLE schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), version INTEGER NOT NULL)"
)

_V1_DDL: tuple[str, ...] = (
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
        display_name TEXT    NOT NULL,
        type         TEXT    NOT NULL CHECK (type IN ('solo', 'team')),
        team_size    INTEGER NOT NULL,
        status       TEXT    NOT NULL CHECK (status IN ('active', 'dnf')),
        dnf_at       INTEGER,
        notes        TEXT,
        logo_card    TEXT,
        UNIQUE (ride_id, plate)
    )
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

_V1_RIDE_ROW = """
    INSERT INTO ride (
        id, name, event_date, venue, course_name, lap_km, organizer, scorer,
        logo_png, planned_start, planned_duration_s, actual_start, finished_at,
        status, entry_mode, max_team_size, plate_model, min_lap_s, deck_count,
        jokers_per_deck, jokers_mode, max_cards, tiebreak_order, rng_seed,
        created_at, updated_at, hold_short_laps
    ) VALUES (
        1, 'GORBA EPIC 2026', '2026-09-20', 'Sea to Sky Gondola', 'Gondola Loop',
        8.0, 'GORBA', 'K. Singh', NULL, 1789898400, 21600, 1789898400, NULL,
        'running', 'mixed', 4, 'rider_pooled', 1080, 8, 2, 'total', NULL,
        '["laps","total_time","high_card"]', 20260920, 1789898400, 1789898400, 1
    )
"""

# Three entries: a solo entry with recorded data, a team entry, and a
# DNF solo -- the id/plate/name/status spread the migration must copy.
_V1_ENTRY_ROWS: tuple[str, ...] = (
    """
    INSERT INTO entry (id, ride_id, plate, display_name, type, team_size,
                       status, dnf_at, notes, logo_card)
    VALUES (1, 1, '12', 'Alice', 'solo', 1, 'active', NULL, '', NULL)
    """,
    """
    INSERT INTO entry (id, ride_id, plate, display_name, type, team_size,
                       status, dnf_at, notes, logo_card)
    VALUES (2, 1, '45', 'Dirt Dynamos', 'team', 2, 'active', NULL,
            'carry a tail light', NULL)
    """,
    """
    INSERT INTO entry (id, ride_id, plate, display_name, type, team_size,
                       status, dnf_at, notes, logo_card)
    VALUES (3, 1, '9', 'Bo', 'solo', 1, 'dnf', 1789899000, 'cramp', 'As')
    """,
)

# The child rows whose ``entry_id`` links the entry rebuild must keep
# resolving: 4 riders, 3 crossings, 3 cards, one open app_session and
# three audit rows -- the display-only ``start_ride`` a v1 build wrote,
# plus the ``start``/``record_crossing`` pair the replay seam needs, the
# crossing still naming Alice's entry by her plate ("12") the way v1
# wrote it (the v2 -> v3 step's own seed).
_V1_CHILD_ROWS: tuple[str, ...] = (
    """
    INSERT INTO rider (id, entry_id, first_name, last_name, plate, sort_order,
                       sex, emergency_contact, waiver_signed, ccn_reg_id)
    VALUES (1, 1, 'Alice', 'Wong', '12', 0, 'F', '604-555-0101', 1, 'CCN-77')
    """,
    """
    INSERT INTO rider (id, entry_id, first_name, last_name, plate, sort_order,
                       sex, emergency_contact, waiver_signed, ccn_reg_id)
    VALUES (2, 2, 'Sarah', 'Roy', '45', 0, 'F', NULL, NULL, NULL)
    """,
    """
    INSERT INTO rider (id, entry_id, first_name, last_name, plate, sort_order,
                       sex, emergency_contact, waiver_signed, ccn_reg_id)
    VALUES (3, 2, 'Priya', 'Nair', '46', 1, 'F', NULL, NULL, NULL)
    """,
    """
    INSERT INTO rider (id, entry_id, first_name, last_name, plate, sort_order,
                       sex, emergency_contact, waiver_signed, ccn_reg_id)
    VALUES (4, 3, 'Bo', 'Diaz', '9', 0, 'M', NULL, NULL, NULL)
    """,
    """
    INSERT INTO crossing (id, ride_id, entry_id, rider_id, seq, crossed_at,
                          lap_s, flag, voided, void_reason)
    VALUES (1, 1, 1, 1, 1, 1789898520, 120, 'none', 0, NULL)
    """,
    """
    INSERT INTO crossing (id, ride_id, entry_id, rider_id, seq, crossed_at,
                          lap_s, flag, voided, void_reason)
    VALUES (2, 1, 1, 1, 2, 1789898640, 132, 'none', 0, NULL)
    """,
    """
    INSERT INTO crossing (id, ride_id, entry_id, rider_id, seq, crossed_at,
                          lap_s, flag, voided, void_reason)
    VALUES (3, 1, 2, 2, 1, 1789898700, 180, 'short', 1, 'cut the loop')
    """,
    """
    INSERT INTO card (id, ride_id, entry_id, crossing_id, shoe_index, rank,
                      suit, state, dealt_at)
    VALUES (1, 1, 1, 1, 0, 14, 's', 'dealt', 1789898520)
    """,
    """
    INSERT INTO card (id, ride_id, entry_id, crossing_id, shoe_index, rank,
                      suit, state, dealt_at)
    VALUES (2, 1, 1, 2, 1, 3, 'h', 'dealt', 1789898640)
    """,
    """
    INSERT INTO card (id, ride_id, entry_id, crossing_id, shoe_index, rank,
                      suit, state, dealt_at)
    VALUES (3, 1, 2, 3, 2, 0, NULL, 'held', 1789898700)
    """,
    """
    INSERT INTO app_session (id, opened_at, closed_at, active_ride_id, heartbeat_at)
    VALUES (1, 1789898000, NULL, 1, 1789898900)
    """,
    """
    INSERT INTO audit (id, ride_id, at, action, payload_json)
    VALUES (1, 1, 1789898400, 'start_ride', '{"source": "setup"}')
    """,
    """
    INSERT INTO audit (id, ride_id, at, action, payload_json)
    VALUES (2, 1, 1789898400, 'start', '{"actual_start": "2026-09-20T10:00:00"}')
    """,
    """
    INSERT INTO audit (id, ride_id, at, action, payload_json)
    VALUES (3, 1, 1789898520, 'record_crossing',
            '{"plate": "12", "entry_id": "12", "lap": 1,
              "crossed_at": "2026-09-20T10:02:00", "reason": "Alice"}')
    """,
)

_V1_SEED: tuple[str, ...] = (_V1_RIDE_ROW, *_V1_ENTRY_ROWS, *_V1_CHILD_ROWS)

# One lone entry, and one no-entry ride: the single/many/empty boundary
# rows a rebuild has to copy (or not) without special-casing.
_V1_SINGLE_ENTRY_SEED: tuple[str, ...] = (
    _V1_RIDE_ROW,
    """
    INSERT INTO entry (id, ride_id, plate, display_name, type, team_size,
                       status, dnf_at, notes, logo_card)
    VALUES (1, 1, '12', 'Alice', 'solo', 1, 'active', NULL, '', NULL)
    """,
)

# A v1 file whose rider row points at an entry that does not exist (a
# corruption an FK-off writer can leave behind). The rebuild's
# ``foreign_key_check`` must catch it rather than paper over it.
_V1_ORPHAN_SEED: tuple[str, ...] = (
    _V1_RIDE_ROW,
    """
    INSERT INTO rider (id, entry_id, first_name, last_name, plate, sort_order)
    VALUES (1, 7, 'Ghost', 'Rider', '7', 0)
    """,
)


def _write_v1_file(db_path: Path, seed: tuple[str, ...] = ()) -> None:
    """Write a released-build v1 database file (arrange).

    The frozen v1 DDL, the ledger stamped 1, then the *seed* rows -- the
    exact file a 1.0.x build left on disk.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        for statement in (*_V1_DDL, _V1_LEDGER_DDL, "INSERT INTO schema_version VALUES (1, 1)"):
            conn.execute(statement)
        for statement in seed:
            conn.execute(statement)
        conn.commit()
    finally:
        conn.close()


def _read(db_path: Path, sql: str, params: tuple[object, ...] = ()) -> list[tuple[object, ...]]:
    """Run one read-only query against *db_path* (assertion aid).

    Reads through a second, independent connection: the point is what
    landed on disk, not what the facade keeps in memory.
    """
    with closing(sqlite3.connect(str(db_path))) as conn:
        return [tuple(row) for row in conn.execute(sql, params)]


def _audit_payload(db_path: Path, audit_id: int) -> dict[str, object]:
    """Return one stored audit payload, decoded (assertion aid)."""
    (raw,) = _read(db_path, "SELECT payload_json FROM audit WHERE id = ?", (audit_id,))[0]
    return dict(json.loads(str(raw)))


def test_store_open_migrates_a_v1_file_to_the_current_schema_version(tmp_path: Path) -> None:
    """A v1 file is upgraded in place, not refused."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)

    Store.open(db_path).close()

    assert _read(db_path, "SELECT version FROM schema_version WHERE id = 1") == [(3,)]


def test_store_open_v1_migration_rebuilds_entry_in_the_v2_shape(tmp_path: Path) -> None:
    """The rebuilt entry table is the v2 column list, in order."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)

    Store.open(db_path).close()

    columns = [row[1] for row in _read(db_path, "PRAGMA table_info(entry)")]

    assert columns == [
        "id",
        "ride_id",
        "plate",
        "key",
        "display_name",
        "type",
        "team_size",
        "status",
        "dnf_at",
        "notes",
        "logo_card",
        "retired",
    ]


def test_store_open_v1_migration_mints_a_fresh_uuid4_key_per_entry(tmp_path: Path) -> None:
    """Every migrated entry gets a fresh key in Entry.key's own format.

    ``uuid.uuid4().hex`` is the expression ``roster.Entry.key`` mints
    with (32 lowercase hex digits, version nibble 4), so a migrated key
    is indistinguishable from one the roster would have drawn.
    """
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)

    Store.open(db_path).close()

    keys = [row[0] for row in _read(db_path, "SELECT key FROM entry ORDER BY id")]
    parsed = [uuid.UUID(str(key)) for key in keys]

    assert [(entry.version, entry.hex) for entry in parsed] == [(4, str(key)) for key in keys]
    assert len(set(keys)) == len(keys) == 3


def test_store_open_v1_migration_marks_every_migrated_entry_live(tmp_path: Path) -> None:
    """Every migrated entry is live: retired = 0."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)

    Store.open(db_path).close()

    assert _read(db_path, "SELECT id, retired FROM entry ORDER BY id") == [
        (1, 0),
        (2, 0),
        (3, 0),
    ]


def test_store_open_v1_migration_preserves_every_v1_entry_column(tmp_path: Path) -> None:
    """Ids, plates, names, types, sizes and notes survive."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)

    Store.open(db_path).close()

    rows = _read(
        db_path,
        "SELECT id, ride_id, plate, display_name, type, team_size, status,"
        " dnf_at, notes, logo_card FROM entry ORDER BY id",
    )

    assert rows == [
        (1, 1, "12", "Alice", "solo", 1, "active", None, "", None),
        (2, 1, "45", "Dirt Dynamos", "team", 2, "active", None, "carry a tail light", None),
        (3, 1, "9", "Bo", "solo", 1, "dnf", 1789899000, "cramp", "As"),
    ]


def test_store_open_v1_migration_preserves_the_links_child_rows_hold(tmp_path: Path) -> None:
    """Rider, crossing and card rows still resolve the same entry ids.

    The rebuild keeps each entry's ``id`` because these three tables
    reference it; a fresh id would silently orphan every recorded lap.
    """
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)

    Store.open(db_path).close()

    violations = _read(db_path, "PRAGMA foreign_key_check")
    rider_links = _read(db_path, "SELECT id, entry_id FROM rider ORDER BY id")
    crossing_links = _read(db_path, "SELECT id, entry_id FROM crossing ORDER BY id")
    card_links = _read(db_path, "SELECT id, entry_id FROM card ORDER BY id")

    assert (violations, rider_links, crossing_links, card_links) == (
        [],
        [(1, 1), (2, 2), (3, 2), (4, 3)],
        [(1, 1), (2, 1), (3, 2)],
        [(1, 1), (2, 1), (3, 2)],
    )


def test_store_open_v1_migration_leaves_only_the_live_plate_unique_index(tmp_path: Path) -> None:
    """v1's inline UNIQUE is gone; the partial index replaces it."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)

    Store.open(db_path).close()

    indexes = {row[1]: (row[2], row[4]) for row in _read(db_path, "PRAGMA index_list(entry)")}
    index_columns = [
        row[2] for row in _read(db_path, "PRAGMA index_info(entry_plate_live_unique)")
    ]

    assert indexes == {"entry_plate_live_unique": (1, 1)}
    assert index_columns == ["ride_id", "plate"]


def test_store_open_v1_migration_enforces_plate_uniqueness_among_live_rows(
    tmp_path: Path,
) -> None:
    """After migration two live entries may not share a ride's plate."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)
    Store.open(db_path).close()

    with (
        closing(sqlite3.connect(str(db_path))) as conn,
        pytest.raises(
            sqlite3.IntegrityError,
            match=re.escape("UNIQUE constraint failed: entry.ride_id, entry.plate"),
        ),
    ):
        conn.execute(
            "INSERT INTO entry (ride_id, plate, key, display_name, type,"
            " team_size, status, retired)"
            " VALUES (1, '12', 'a' * 32, 'Ghost', 'solo', 1, 'active', 0)"
        )


def test_store_open_v1_migration_allows_a_retired_row_to_hold_a_live_plate(
    tmp_path: Path,
) -> None:
    """The partial index leaves retired plates free (S1's move seam)."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)
    Store.open(db_path).close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            "INSERT INTO entry (ride_id, plate, key, display_name, type,"
            " team_size, status, retired)"
            " VALUES (1, '12', 'b' * 32, 'Alice', 'solo', 1, 'active', 1)"
        )
        conn.commit()

    assert _read(
        db_path, "SELECT plate, retired FROM entry WHERE ride_id = 1 AND plate = '12' ORDER BY id"
    ) == [("12", 0), ("12", 1)]


def test_store_open_v1_migration_leaves_entry_matching_a_fresh_file(tmp_path: Path) -> None:
    """A migrated file's entry shape matches a fresh file's.

    The migration's ``entry`` DDL is frozen at v2 while
    ``SCHEMA_STATEMENTS`` is edited in place by later versions, so this
    pins the two together: a v3 edit that forgot its own migration (or a
    migration drift) fails here rather than in the field.
    """
    migrated_path = tmp_path / "v1.db"
    _write_v1_file(migrated_path, seed=_V1_SEED)
    Store.open(migrated_path).close()
    fresh_path = tmp_path / "fresh.db"
    Store.open(fresh_path).close()

    migrated = (
        _read(migrated_path, "PRAGMA table_info(entry)"),
        sorted(_read(migrated_path, "PRAGMA index_list(entry)")),
    )
    fresh = (
        _read(fresh_path, "PRAGMA table_info(entry)"),
        sorted(_read(fresh_path, "PRAGMA index_list(entry)")),
    )

    assert migrated == fresh


def test_store_open_v1_migration_given_no_entry_rows_migrates_cleanly(tmp_path: Path) -> None:
    """No entries at all rebuilds to an empty v2 table."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=(_V1_RIDE_ROW,))

    Store.open(db_path).close()

    assert (
        _read(db_path, "SELECT COUNT(*) FROM entry"),
        _read(db_path, "SELECT version FROM schema_version WHERE id = 1"),
    ) == ([(0,)], [(3,)])


def test_store_open_v1_migration_given_one_entry_row_copies_it(tmp_path: Path) -> None:
    """A single v1 entry row survives with its id and plate intact."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SINGLE_ENTRY_SEED)

    Store.open(db_path).close()

    assert _read(db_path, "SELECT id, plate, retired FROM entry") == [(1, "12", 0)]


def test_store_open_v1_migration_with_an_orphan_child_row_raises_and_rolls_back(
    tmp_path: Path,
) -> None:
    """An FK the rebuild cannot honour aborts the migration."""
    db_path = tmp_path / "orphan.db"
    _write_v1_file(db_path, seed=_V1_ORPHAN_SEED)

    with pytest.raises(sqlite3.IntegrityError, match=re.escape("foreign key violations in rider")):
        Store.open(db_path)

    version = _read(db_path, "SELECT version FROM schema_version WHERE id = 1")
    columns = [row[1] for row in _read(db_path, "PRAGMA table_info(entry)")]

    assert (version, columns) == (
        [(1,)],
        [
            "id",
            "ride_id",
            "plate",
            "display_name",
            "type",
            "team_size",
            "status",
            "dnf_at",
            "notes",
            "logo_card",
        ],
    )


def test_run_migrations_stamps_the_ledger_at_the_target_version(tmp_path: Path) -> None:
    """Each step runs in order, then the ledger is stamped."""
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)

    conn = sqlite3.connect(str(db_path))
    try:
        run_migrations(conn, 1, SCHEMA_VERSION)
    finally:
        conn.close()

    assert (
        _read(db_path, "SELECT version FROM schema_version WHERE id = 1"),
        _read(db_path, "SELECT COUNT(*) FROM entry WHERE key <> ''"),
    ) == ([(3,)], [(3,)])


def test_run_migrations_from_the_target_version_changes_nothing(tmp_path: Path) -> None:
    """An empty range is a no-op: the data is untouched."""
    db_path = tmp_path / "v2.db"
    Store.open(db_path).close()
    before = _read(db_path, "SELECT id, key FROM entry")

    conn = sqlite3.connect(str(db_path))
    try:
        run_migrations(conn, SCHEMA_VERSION, SCHEMA_VERSION)
    finally:
        conn.close()

    assert (
        _read(db_path, "SELECT version FROM schema_version WHERE id = 1"),
        _read(db_path, "SELECT id, key FROM entry"),
    ) == ([(3,)], before)


def test_run_migrations_given_a_version_below_the_first_migration_raises_key_error(
    tmp_path: Path,
) -> None:
    """Version 0 is the create path, not a migration.

    ``range(0, 2)`` asks for a v0 -> v1 step that cannot exist (v0
    is an empty file, which :func:`ensure_schema` creates rather than
    migrates), so the missing registry entry surfaces as a KeyError
    naming it.
    """
    db_path = tmp_path / "v0.db"
    sqlite3.connect(str(db_path)).close()
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(SCHEMA_VERSION_DDL)

        with pytest.raises(KeyError, match=re.escape("0")):
            run_migrations(conn, 0, SCHEMA_VERSION)
    finally:
        conn.close()


def test_migrations_cover_every_version_below_the_current_one() -> None:
    """Every version below SCHEMA_VERSION has a step."""
    assert sorted(MIGRATIONS) == list(range(1, SCHEMA_VERSION))


# ------------------------------------------------ v2 -> v3 migration
# Product-owner policy (spec §2) again. v2 rebuilt ``entry`` around
# the stable ``key`` but left the ``audit`` payloads alone, so a file
# the pooled-live-move branch wrote still names its entry by plate
# where the replay seam resolves a key -- the "unknown entry key: 93"
# a pre-branch ride refuses to open with. v3 changes no DDL: it
# rewrites those payloads in place. These tests build a v2 file from
# the CURRENT ``SCHEMA_STATEMENTS`` (v3 adds nothing to them) stamped
# 2, and drive it through ``Store.open``, which runs that one step.

# The stable keys a v2 file's entries carry -- the identity a legacy
# payload has to be rewritten to. The third belongs to a second ride's
# own entry at plate "12": plate numbers are unique per ride.
_V2_ALICE_KEY = "0f1e2d3c4b5a49788796a5b4c3d2e1f0"
_V2_DYNAMOS_KEY = "1a2b3c4d5e6f408192a3b4c5d6e7f809"
_V2_RIDE2_KEY = "2b3c4d5e6f704192a3b4c5d6e7f8091a"

# ``entry`` rows as (id, ride_id, plate, key, display_name, type,
# team_size, retired).
_V2_ALICE_ENTRY: tuple[object, ...] = (1, 1, "12", _V2_ALICE_KEY, "Alice", "solo", 1, 0)
_V2_DYNAMOS_ENTRY: tuple[object, ...] = (2, 1, "45", _V2_DYNAMOS_KEY, "Dirt Dynamos", "team", 2, 0)
_V2_RETIRED_ALICE_ENTRY: tuple[object, ...] = (1, 1, "12", _V2_ALICE_KEY, "Alice", "solo", 1, 1)
_V2_RIDE2_ALICE_ENTRY: tuple[object, ...] = (3, 2, "12", _V2_RIDE2_KEY, "Alice", "solo", 1, 0)

# ``audit`` rows as (id, ride_id, at, action, payload_json). The start
# row is what the replay seam needs to reach RUNNING before a crossing,
# and the crossing is the legacy shape under test: the plated identity
# a pre-v2 build wrote beside the plate the operator typed. The last
# row is the same shape on a second ride, whose entry "12" is another
# ride's team entirely.
_V2_START_EVENT: tuple[object, ...] = (
    1,
    1,
    1789898400,
    "start",
    '{"actual_start": "2026-09-20T10:00:00"}',
)
_V2_LEGACY_CROSSING_EVENT: tuple[object, ...] = (
    2,
    1,
    1789898520,
    "record_crossing",
    (
        '{"plate": "12", "entry_id": "12", "lap": 1,'
        ' "crossed_at": "2026-09-20T10:02:00", "reason": "Alice"}'
    ),
)
_V2_SECOND_RIDE_CROSSING_EVENT: tuple[object, ...] = (
    6,
    2,
    1789898520,
    "record_crossing",
    '{"plate": "12", "entry_id": "12", "lap": 1, "reason": "Alice"}',
)

# A payload naming a plate no live entry holds, a payload this branch
# itself wrote (its identity is already a key), and a pooled move's own
# row naming both the source and the destination.
_V2_UNKNOWN_IDENTITY_EVENT: tuple[object, ...] = (
    3,
    1,
    1789898580,
    "dnf",
    '{"entry_id": "99", "plate": "99", "rider": false, "reason": "no show"}',
)
_V2_ALREADY_KEYED_EVENT: tuple[object, ...] = (
    4,
    1,
    1789898640,
    "void_card",
    json.dumps({"entry_id": _V2_ALICE_KEY, "card": "As", "reason": "duplicate"}),
)
# A hand-edited payload whose identity is a number, not text: left as
# stored, like every other value this step cannot resolve.
_V2_NON_STRING_IDENTITY_EVENT: tuple[object, ...] = (
    5,
    1,
    1789898700,
    "record_crossing",
    '{"entry_id": 93, "plate": "93", "reason": "hand-edited"}',
)
_V2_REASSIGN_EVENT: tuple[object, ...] = (
    2,
    1,
    1789898520,
    "reassign",
    '{"seq": 1, "old_entry_id": "12", "new_entry_id": "45", "new_plate": "45", "reason": "moved"}',
)

# The two payload shapes the rewrite must decline to touch rather than
# abort the whole file on: text that is not JSON, and JSON that is not
# an object. ``Store.load_engine`` already reports both cleanly.
_V2_MALFORMED_EVENT: tuple[object, ...] = (4, 1, 1789898640, "record_crossing", "not json at all")
_V2_NON_OBJECT_EVENT: tuple[object, ...] = (5, 1, 1789898700, "record_crossing", "[]")

# A second ride in the same file, copied from the first: plate numbers
# are unique per ride, so the same number names a different entry here.
_V2_SECOND_RIDE_ROW = """
    INSERT INTO ride
    SELECT 2, 'GORBA EPIC 2026 (2)', event_date, venue, course_name, lap_km,
           organizer, scorer, logo_png, planned_start, planned_duration_s,
           actual_start, finished_at, status, entry_mode, max_team_size,
           plate_model, min_lap_s, deck_count, jokers_per_deck, jokers_mode,
           max_cards, tiebreak_order, rng_seed, created_at, updated_at,
           hold_short_laps
      FROM ride WHERE id = 1
    """

_V2_ENTRY_INSERT_SQL = (
    "INSERT INTO entry (id, ride_id, plate, key, display_name, type, team_size,"
    " status, dnf_at, notes, logo_card, retired)"
    " VALUES (?, ?, ?, ?, ?, ?, ?, 'active', NULL, '', NULL, ?)"
)

_V2_AUDIT_INSERT_SQL = (
    "INSERT INTO audit (id, ride_id, at, action, payload_json) VALUES (?, ?, ?, ?, ?)"
)


def _write_v2_file(  # noqa: PLR0913 -- a file's own parts: entries, audit, extra rides
    db_path: Path,
    *,
    entries: tuple[tuple[object, ...], ...],
    audit: tuple[tuple[object, ...], ...],
    extra_rides: tuple[str, ...] = (),
) -> None:
    """Write a v2 database file (arrange).

    The CURRENT ``SCHEMA_STATEMENTS`` -- v3 changes no DDL, so they are
    the v2 shape exactly -- stamped 2, then the caller's ``entry`` and
    ``audit`` rows: the file this branch left on disk before the audit
    rewrite, whose payloads still name entries by plate. ``ride``'s DDL
    is unchanged since v1, so the released v1 ride row seeds ride 1,
    with *extra_rides* adding any others.
    """
    conn = sqlite3.connect(str(db_path))
    try:
        for statement in (*SCHEMA_STATEMENTS, SCHEMA_VERSION_DDL):
            conn.execute(statement)
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 2)")
        for statement in (_V1_RIDE_ROW, *extra_rides):
            conn.execute(statement)
        for entry in entries:
            conn.execute(_V2_ENTRY_INSERT_SQL, entry)
        for audit_row in audit:
            conn.execute(_V2_AUDIT_INSERT_SQL, audit_row)
        conn.commit()
    finally:
        conn.close()


def test_store_open_v1_migration_rewrites_a_legacy_audit_plate_to_its_key(
    tmp_path: Path,
) -> None:
    """A v1 file's plated audit identities come back as keys.

    The user-visible symptom is the ride that would not reopen:
    ``load_engine`` resolved the payload's legacy plate as a key and
    failed on "unknown entry key: 12".
    """
    db_path = tmp_path / "v1.db"
    _write_v1_file(db_path, seed=_V1_SEED)

    store = Store.open(db_path)
    try:
        engine = store.load_engine(1)
    finally:
        store.close()

    (alice_key,) = _read(db_path, "SELECT key FROM entry WHERE ride_id = 1 AND plate = '12'")[0]

    assert (_audit_payload(db_path, 3)["entry_id"], engine.state) == (
        alice_key,
        RideStatus.RUNNING,
    )


def test_store_open_v2_file_rewrites_a_legacy_audit_plate_to_its_key(tmp_path: Path) -> None:
    """The pre-branch user's own state: a file stamped 2 runs v2 -> v3.

    No chain step can precede it on such a file, and the ride has to
    come back replayable -- which is the whole point of the step.
    """
    db_path = tmp_path / "v2.db"
    _write_v2_file(
        db_path,
        entries=(_V2_ALICE_ENTRY,),
        audit=(_V2_START_EVENT, _V2_LEGACY_CROSSING_EVENT),
    )

    store = Store.open(db_path)
    try:
        engine = store.load_engine(1)
    finally:
        store.close()

    assert (
        _audit_payload(db_path, 2)["entry_id"],
        _read(db_path, "SELECT version FROM schema_version WHERE id = 1"),
        engine.state,
    ) == (_V2_ALICE_KEY, [(3,)], RideStatus.RUNNING)


def test_store_open_v2_to_v3_rewrites_only_a_plated_entry_identity(tmp_path: Path) -> None:
    """Only the identity fields change, and only from live plates.

    The operator's typed ``plate`` and ``reason`` survive verbatim, and
    a row needing no rewrite is not written back at all -- a value
    already holding a key, naming nothing in this ride, or not text at
    all is left exactly as it was stored.
    """
    db_path = tmp_path / "v2.db"
    _write_v2_file(
        db_path,
        entries=(_V2_ALICE_ENTRY,),
        audit=(
            _V2_START_EVENT,
            _V2_LEGACY_CROSSING_EVENT,
            _V2_UNKNOWN_IDENTITY_EVENT,
            _V2_ALREADY_KEYED_EVENT,
            _V2_NON_STRING_IDENTITY_EVENT,
        ),
    )
    untouched = _read(db_path, "SELECT id, payload_json FROM audit WHERE id > 2 ORDER BY id")

    Store.open(db_path).close()

    payload = _audit_payload(db_path, 2)

    assert (
        payload["entry_id"],
        payload["plate"],
        payload["reason"],
        _read(db_path, "SELECT id, payload_json FROM audit WHERE id > 2 ORDER BY id"),
    ) == (_V2_ALICE_KEY, "12", "Alice", untouched)


def test_store_open_v2_to_v3_given_a_second_open_leaves_the_audit_untouched(
    tmp_path: Path,
) -> None:
    """Re-opening rewrites nothing: the rewritten rows hold keys now."""
    db_path = tmp_path / "v2.db"
    _write_v2_file(
        db_path,
        entries=(_V2_ALICE_ENTRY,),
        audit=(_V2_START_EVENT, _V2_LEGACY_CROSSING_EVENT),
    )
    Store.open(db_path).close()
    rewritten = _read(db_path, "SELECT id, payload_json FROM audit ORDER BY id")

    Store.open(db_path).close()

    assert (
        _audit_payload(db_path, 2)["entry_id"],
        _read(db_path, "SELECT id, payload_json FROM audit ORDER BY id"),
    ) == (_V2_ALICE_KEY, rewritten)


def test_store_open_v2_to_v3_rewrites_both_identities_in_a_reassign_payload(
    tmp_path: Path,
) -> None:
    """A pooled move's old and new entry ids are rewritten together.

    ``new_plate`` is the operator's typed value, not an identity, so it
    is left alone.
    """
    db_path = tmp_path / "v2.db"
    _write_v2_file(
        db_path,
        entries=(_V2_ALICE_ENTRY, _V2_DYNAMOS_ENTRY),
        audit=(_V2_START_EVENT, _V2_REASSIGN_EVENT),
    )

    Store.open(db_path).close()

    payload = _audit_payload(db_path, 2)

    assert (
        payload["old_entry_id"],
        payload["new_entry_id"],
        payload["new_plate"],
    ) == (_V2_ALICE_KEY, _V2_DYNAMOS_KEY, "45")


def test_store_open_v2_to_v3_resolves_each_rides_plates_separately(tmp_path: Path) -> None:
    """Plates are unique per ride, so a rewrite is scoped by ride.

    Both rides number an entry "12", and each legacy payload has to
    come back as its own ride's key -- never the other ride's.
    """
    db_path = tmp_path / "v2.db"
    _write_v2_file(
        db_path,
        entries=(_V2_ALICE_ENTRY, _V2_RIDE2_ALICE_ENTRY),
        audit=(_V2_LEGACY_CROSSING_EVENT, _V2_SECOND_RIDE_CROSSING_EVENT),
        extra_rides=(_V2_SECOND_RIDE_ROW,),
    )

    Store.open(db_path).close()

    assert (
        _audit_payload(db_path, 2)["entry_id"],
        _audit_payload(db_path, 6)["entry_id"],
    ) == (_V2_ALICE_KEY, _V2_RIDE2_KEY)


def test_store_open_v2_to_v3_skips_a_malformed_payload_without_aborting(tmp_path: Path) -> None:
    """One unusable payload neither strands the file nor is corrupted.

    Both shapes are skipped and left exactly as they were written, so
    the clean ``StoreError`` replay reports them with still fires --
    never a migration aborted halfway through the table.
    """
    db_path = tmp_path / "v2.db"
    _write_v2_file(
        db_path,
        entries=(_V2_ALICE_ENTRY,),
        audit=(
            _V2_START_EVENT,
            _V2_MALFORMED_EVENT,
            _V2_LEGACY_CROSSING_EVENT,
            _V2_NON_OBJECT_EVENT,
        ),
    )

    store = Store.open(db_path)
    try:
        with pytest.raises(StoreError, match=re.escape("cannot replay audit row 4 for ride 1")):
            store.load_engine(1)
    finally:
        store.close()

    assert (
        _read(db_path, "SELECT id, payload_json FROM audit WHERE id > 3 ORDER BY id"),
        _audit_payload(db_path, 2)["entry_id"],
    ) == ([(4, "not json at all"), (5, "[]")], _V2_ALICE_KEY)


def test_store_open_v2_to_v3_leaves_the_entry_rows_untouched(tmp_path: Path) -> None:
    """The step rewrites audit payloads alone: no entry row changes.

    v3 adds no DDL, so a v3 file's ``entry`` rows are the ones it
    opened with, and the ledger is stamped 3 once the chain finished.
    """
    db_path = tmp_path / "v2.db"
    _write_v2_file(
        db_path,
        entries=(_V2_ALICE_ENTRY, _V2_DYNAMOS_ENTRY),
        audit=(_V2_START_EVENT, _V2_LEGACY_CROSSING_EVENT),
    )

    Store.open(db_path).close()

    assert (
        _read(db_path, "SELECT id, plate, key, retired FROM entry ORDER BY id"),
        _read(db_path, "SELECT version FROM schema_version WHERE id = 1"),
    ) == (
        [(1, "12", _V2_ALICE_KEY, 0), (2, "45", _V2_DYNAMOS_KEY, 0)],
        [(3,)],
    )


def test_store_open_v2_to_v3_leaves_a_retired_entrys_plate_unresolved(tmp_path: Path) -> None:
    """A retired row is no identity source: only live plates map.

    The plate a retired row kept may be the one the destination team
    has since adopted, so a legacy value only a retired row could
    explain is left exactly as stored rather than guessed at -- while
    the same payload's live identity is rewritten as usual.
    """
    db_path = tmp_path / "v2.db"
    _write_v2_file(
        db_path,
        entries=(_V2_RETIRED_ALICE_ENTRY, _V2_DYNAMOS_ENTRY),
        audit=(_V2_REASSIGN_EVENT,),
    )

    Store.open(db_path).close()

    payload = _audit_payload(db_path, 2)

    assert (payload["old_entry_id"], payload["new_entry_id"]) == ("12", _V2_DYNAMOS_KEY)


def test_store_open_v2_to_v3_given_a_failed_rewrite_rolls_the_file_back(
    tmp_path: Path,
) -> None:
    """A rewrite the step cannot land leaves the v2 file as it was.

    The trigger stands in for any write error (a full disk, a locked
    file): the step's own BEGIN/rollback must leave the ledger and the
    payloads exactly as the file's owner saved them.
    """
    db_path = tmp_path / "v2.db"
    _write_v2_file(
        db_path,
        entries=(_V2_ALICE_ENTRY,),
        audit=(_V2_LEGACY_CROSSING_EVENT,),
    )
    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.execute(
            "CREATE TRIGGER audit_payload_frozen BEFORE UPDATE ON audit"
            " BEGIN SELECT RAISE(ABORT, 'audit payload frozen'); END"
        )
        conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match=re.escape("audit payload frozen")):
        Store.open(db_path)

    assert (
        _read(db_path, "SELECT version FROM schema_version WHERE id = 1"),
        _audit_payload(db_path, 2)["entry_id"],
    ) == ([(2,)], "12")


# --------------------------------------------------------- create_ride


def test_store_create_ride_round_trips_summary_row(tmp_path: Path) -> None:
    """create_ride returns an id; rides() reports name, date, status."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="GORBA EPIC 2026"))
    finally:
        store.close()

    store = Store.open(db_path)
    try:
        assert store.rides() == [
            RideRow(
                id=ride_id,
                name="GORBA EPIC 2026",
                event_date=date(2026, 9, 20),
                status=RideStatus.DRAFT,
            )
        ]
    finally:
        store.close()


def test_store_create_ride_sets_db_owned_rng_seed(tmp_path: Path) -> None:
    """rng_seed is store-generated, never taken from the config."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert isinstance(row["rng_seed"], int)
    assert row["rng_seed"] > 0


def test_store_create_ride_honours_an_explicit_rng_seed(tmp_path: Path) -> None:
    """An explicit rng_seed override lands in the ride row verbatim.

    E9.2.2 (R-77): the nightly acceptance race owns its seed -- it
    generates one, injects it here, and files it on failure, so a
    failed night is reproducible by re-running with the same env.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(), rng_seed=20260920)
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert row["rng_seed"] == 20260920


def test_store_create_ride_without_a_seed_draws_one_63_bit_os_value(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absent rng_seed draws exactly one 63-bit seed from the CSPRNG.

    T-10 boundary: ``secrets.randbits`` is the OS entropy source, so a
    recording stand-in is the one seam this module fakes -- it pins
    the draw's own width, and that the value it returns is the one
    stored.
    """
    db_path = tmp_path / "rides.db"
    widths: list[int] = []

    def _draw(width: int) -> int:
        widths.append(width)
        return 20260920

    monkeypatch.setattr(store_module.secrets, "randbits", _draw)
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert (widths, row["rng_seed"]) == ([63], 20260920)


def test_store_create_ride_given_an_injected_seed_skips_the_csprng_draw(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An injected rng_seed is stored as given, with no OS draw (T-3).

    The mirror of the pin above: the 63-bit draw happens only when the
    caller offers no seed. ``drawn.append`` returns None, so a stray
    draw would store None and fail the row assertion too.
    """
    db_path = tmp_path / "rides.db"
    drawn: list[int] = []
    monkeypatch.setattr(store_module.secrets, "randbits", drawn.append)
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(), rng_seed=20260920)
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert (drawn, row["rng_seed"]) == ([], 20260920)


def test_store_create_ride_hold_short_laps_column_round_trips(tmp_path: Path) -> None:
    """The W4 policy column stores 1/0 and rebuilds the config field."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        hold_id = store.create_ride(_config(name="Hold Policy", hold_short_laps=True))
        deal_id = store.create_ride(_config(name="Always Deal", hold_short_laps=False))
    finally:
        store.close()

    hold_row = _fetch_ride_row(db_path, hold_id)
    deal_row = _fetch_ride_row(db_path, deal_id)
    assert hold_row["hold_short_laps"] == 1
    assert deal_row["hold_short_laps"] == 0

    reopened = Store.open(db_path)
    try:
        hold_engine = reopened.load_engine(hold_id)
        deal_engine = reopened.load_engine(deal_id)
    finally:
        reopened.close()
    assert hold_engine.config.hold_short_laps is True
    assert deal_engine.config.hold_short_laps is False


def test_store_load_engine_given_a_row_without_a_policy_rebuilds_the_hold_default(
    tmp_path: Path,
) -> None:
    """A row that never set the policy replays as the hold default.

    A ``ride`` row written directly -- not through ``create_ride`` --
    still gets ``hold_short_laps=1`` from the column's NOT NULL
    DEFAULT, so the rebuilt config matches the dialog's own default
    rather than fabricating an always-deal ride the row never recorded.
    """
    db_path = tmp_path / "direct.db"
    conn = sqlite3.connect(str(db_path))
    try:
        for statement in (*SCHEMA_STATEMENTS, SCHEMA_VERSION_DDL):
            conn.execute(statement)
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, ?)", (SCHEMA_VERSION,))
        conn.execute(
            """
            INSERT INTO ride (
                name, event_date, venue, course_name, lap_km, organizer, scorer,
                logo_png, planned_start, planned_duration_s, actual_start,
                finished_at, status, entry_mode, max_team_size, plate_model,
                min_lap_s, deck_count, jokers_per_deck, max_cards, tiebreak_order,
                rng_seed, created_at, updated_at
            ) VALUES (
                'Club night', '2026-09-20', 'Gondola', 'Gondola', 8.0,
                'GORBA', 'K. Singh', NULL, 1789898400, 21600, NULL, NULL,
                'draft', 'mixed', 4, 'rider_pooled', 1080, 8, 2, NULL,
                '["laps","total_time","high_card"]', 20260920, 1789898400, 1789898400
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    store = Store.open(db_path)
    try:
        ride_id = store.rides()[0].id
        engine = store.load_engine(ride_id)
    finally:
        store.close()

    assert engine.config.hold_short_laps is True


def test_store_open_ride_hold_short_laps_column_defaults_to_the_hold_policy(
    tmp_path: Path,
) -> None:
    """The DDL default is 1: hold-for-review, W4's shipped default."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        defaults = {row[1]: row[4] for row in conn.execute("PRAGMA table_info(ride)")}

    assert defaults["hold_short_laps"] == "1"


def test_store_create_ride_stores_logo_blob_round_trip(tmp_path: Path) -> None:
    """A configured logo file is stored as a BLOB, byte-identical."""
    logo_bytes = base64.b64decode(_TINY_PNG_B64)
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(logo_bytes)
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(logo_path=logo_path))
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert row["logo_png"] == logo_bytes


def test_store_create_ride_logo_null_when_no_logo_path(tmp_path: Path) -> None:
    """Without a logo_path the stored logo column is NULL."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert row["logo_png"] is None


def test_store_create_ride_empty_logo_file_stored_as_empty_blob(
    tmp_path: Path,
) -> None:
    """A present-but-empty logo file round-trips as an empty BLOB."""
    empty_path = tmp_path / "empty.png"
    empty_path.write_bytes(b"")
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(logo_path=empty_path))
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert row["logo_png"] == b""


@pytest.mark.parametrize(
    ("max_cards", "expected"),
    [
        (None, None),
        (5, 5),
    ],
)
def test_store_create_ride_max_cards_round_trips_null_vs_set(
    tmp_path: Path,
    max_cards: int | None,
    expected: int | None,
) -> None:
    """max_cards stores NULL uncapped or the integer capped (R-13)."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(max_cards=max_cards))
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert row["max_cards"] == expected


def test_store_create_ride_tiebreak_order_json_round_trip(tmp_path: Path) -> None:
    """tiebreak_order is stored as JSON text and parses back."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(tiebreak_order=("laps", "high_card", "total_time")))
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert isinstance(row["tiebreak_order"], str)
    assert json.loads(row["tiebreak_order"]) == ["laps", "high_card", "total_time"]


def test_store_create_ride_stores_enum_spellings_course_and_epoch_times(
    tmp_path: Path,
) -> None:
    """Enums persist as spellings; venue doubles course."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert row["status"] == "draft"
    assert row["entry_mode"] == "mixed"
    assert row["plate_model"] == "rider_pooled"
    assert row["course_name"] == row["venue"] == "Sea to Sky Gondola"
    assert row["actual_start"] is None
    assert row["finished_at"] is None
    assert isinstance(row["created_at"], int)
    assert isinstance(row["updated_at"], int)
    assert isinstance(row["planned_start"], int)


def test_store_create_ride_aware_planned_start_stored_as_its_epoch(
    tmp_path: Path,
) -> None:
    """An aware planned_start stores as its own UTC epoch."""
    aware_start = datetime(2026, 9, 20, 10, 0, tzinfo=UTC)
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(planned_start=aware_start))
    finally:
        store.close()

    row = _fetch_ride_row(db_path, ride_id)
    assert row["planned_start"] == int(aware_start.timestamp())


def test_store_create_ride_missing_logo_file_fails_loudly(tmp_path: Path) -> None:
    """A missing configured logo file raises, never a silent NULL."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        with pytest.raises(FileNotFoundError, match=re.escape("nope.png")):
            store.create_ride(_config(logo_path=tmp_path / "nope.png"))
        assert store.rides() == []
    finally:
        store.close()


# -------------------------------------------------------------- rides


def test_store_create_ride_distinct_ids_for_multiple_rides(tmp_path: Path) -> None:
    """Each create_ride yields its own ride id."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        first = store.create_ride(_config(name="First"))
        second = store.create_ride(_config(name="Second"))
        assert first != second
        assert {row.id for row in store.rides()} == {first, second}
    finally:
        store.close()


def test_store_rides_orders_by_created_at(tmp_path: Path) -> None:
    """The library lists rides oldest-first by creation order."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        first = store.create_ride(_config(name="First"))
        second = store.create_ride(_config(name="Second"))
        assert [row.id for row in store.rides()] == [first, second]
    finally:
        store.close()


# ----------------------------------------- E5.1.2 append + load_engine


def _replay_roster() -> Roster:
    """Build the MIXED rider_pooled roster load_engine tests pass in."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate="12")
    return roster


def _replay_roster_and_key() -> tuple[Roster, str]:
    """Build the replay roster and its sole entry's key (arrange).

    A persisted payload names its entry by the stable key the entry
    table carries (E3.1.2's pooled-live-move seam), so a hand-written
    ``record_crossing``/``deal_manual`` row has to carry the roster's
    own key for replay to resolve it.
    """
    roster = _replay_roster()
    return roster, entry_key(roster, "12")


def test_store_append_persists_audit_row_with_event_timestamp(tmp_path: Path) -> None:
    """Appending writes one audit row; at uses the event time."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={
                    "plate": "12",
                    "entry_id": "12",
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:02:00",
                },
            ),
        )
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT at, action, payload_json FROM audit WHERE ride_id = ?", (ride_id,)
        ).fetchone()
    assert row is not None
    naive = datetime(2026, 9, 20, 10, 2)  # noqa: DTZ001 -- the naive event timestamp
    assert row["at"] == int(naive.astimezone().timestamp())
    assert row["action"] == "record_crossing"
    assert json.loads(row["payload_json"]) == {
        "plate": "12",
        "entry_id": "12",
        "lap": 1,
        "crossed_at": "2026-09-20T10:02:00",
    }


def test_store_append_event_without_timestamp_stores_now(tmp_path: Path) -> None:
    """A timestamp-less event gets at = append time (now)."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.append(
            ride_id,
            Event(
                action="deal_manual",
                payload={"plate": "12", "entry_id": "12", "card": "AS", "reason": "manual"},
            ),
        )
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT at, action, payload_json FROM audit WHERE ride_id = ?", (ride_id,)
        ).fetchone()
    assert row is not None
    assert isinstance(row["at"], int)
    assert row["at"] > 0
    assert row["action"] == "deal_manual"
    assert json.loads(row["payload_json"])["reason"] == "manual"


def test_store_append_returns_none(tmp_path: Path) -> None:
    """Append is a fire-and-persist call with no return value."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())

        result = store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
    finally:
        store.close()

    assert result is None


def test_store_append_unknown_ride_raises_foreign_key_error(tmp_path: Path) -> None:
    """Appending to a missing ride fails loudly (FK constraint)."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        with pytest.raises(sqlite3.IntegrityError, match=re.escape("FOREIGN KEY")):
            store.append(
                999, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
            )
    finally:
        store.close()


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (
            Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
            RideStatus.RUNNING,
        ),
        (
            Event(action="continue", payload={"actual_start": "2026-09-20T10:00:00"}),
            RideStatus.RUNNING,
        ),
        (
            Event(action="finish", payload={"finished_at": "2026-09-20T12:00:00"}),
            RideStatus.FINISHED,
        ),
        (
            Event(action="reopen", payload={"reopened_at": "2026-09-20T12:05:00"}),
            RideStatus.REOPENED,
        ),
    ],
    ids=["start", "continue", "finish", "reopen"],
)
def test_store_append_lifecycle_action_writes_the_mapped_status(
    tmp_path: Path, event: Event, expected: RideStatus
) -> None:
    """The lifecycle actions sync ride.status in append's transaction.

    start and continue both mean RUNNING, finish FINISHED, reopen
    REOPENED -- and ``rides()`` (the library's own read) shows it.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())

        store.append(ride_id, event)

        rows = store.rides()
    finally:
        store.close()

    assert [(row.id, row.status) for row in rows] == [(ride_id, expected)]


def test_store_append_record_crossing_leaves_status_unchanged(tmp_path: Path) -> None:
    """A crossing is an audit append: status and updated_at stay put.

    The ride starts DRAFT; ``record_crossing`` is not a lifecycle
    action, so the row's status and its updated_at sentinel must both
    survive the append untouched.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        with store._conn:
            store._conn.execute("UPDATE ride SET updated_at = 1 WHERE id = ?", (ride_id,))

        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={
                    "plate": "12",
                    "entry_id": "12",
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:02:00",
                },
            ),
        )

        rows = store.rides()
    finally:
        store.close()

    assert [(row.id, row.status) for row in rows] == [(ride_id, RideStatus.DRAFT)]
    with closing(sqlite3.connect(str(db_path))) as conn:
        updated_at = conn.execute(
            "SELECT updated_at FROM ride WHERE id = ?", (ride_id,)
        ).fetchone()[0]
    assert updated_at == 1  # the sentinel survived: no ride-row write happened


def test_store_append_lifecycle_action_stamps_updated_at(tmp_path: Path) -> None:
    """A lifecycle append writes updated_at = now on the ride row."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        with store._conn:
            store._conn.execute("UPDATE ride SET updated_at = 1 WHERE id = ?", (ride_id,))
        before = int(datetime.now(UTC).timestamp())

        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        updated_at = conn.execute(
            "SELECT updated_at FROM ride WHERE id = ?", (ride_id,)
        ).fetchone()[0]
    assert updated_at >= before


def test_store_load_engine_missing_ride_raises_ride_not_found(tmp_path: Path) -> None:
    """Loading a ride id that never existed fails loudly."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        with pytest.raises(RideNotFoundError, match=re.escape("999")):
            store.load_engine(999, _replay_roster())
    finally:
        store.close()


def test_store_load_engine_replays_start_and_crossing_into_running_engine(
    tmp_path: Path,
) -> None:
    """Loading rebuilds a RUNNING engine with crossings and events."""
    roster, key = _replay_roster_and_key()
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        start_event = Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        crossing_event = Event(
            action="record_crossing",
            payload={
                "plate": "12",
                "entry_id": key,
                "lap": 1,
                "crossed_at": "2026-09-20T10:02:00",
            },
        )
        store.append(ride_id, start_event)
        store.append(ride_id, crossing_event)

        engine = store.load_engine(ride_id, roster)
    finally:
        store.close()

    assert engine.state is RideStatus.RUNNING
    assert len(engine.crossings) == 1
    assert engine.crossings[0].entry_id == key
    assert engine.crossings[0].crossed_at == datetime(2026, 9, 20, 10, 2)  # noqa: DTZ001
    # Replay re-derives each event's own reason from the roster, so the
    # persisted payloads' actions survive the round trip (scope 6d).
    assert [(event.action, event.payload["reason"]) for event in engine.events] == [
        ("start", "0:00:00"),
        ("record_crossing", "Alice · solo"),
    ]


def test_store_load_engine_replays_single_start_event_into_running_engine(
    tmp_path: Path,
) -> None:
    """A one-event stream (just start) replays into a RUNNING engine."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        start_event = Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        store.append(ride_id, start_event)

        engine = store.load_engine(ride_id, _replay_roster())
    finally:
        store.close()

    assert engine.state is RideStatus.RUNNING
    assert engine.events == (
        Event(
            action="start",
            payload={"actual_start": "2026-09-20T10:00:00", "reason": "0:00:00"},
        ),
    )
    assert engine.crossings == ()


def test_store_load_engine_replays_in_append_order_not_at_order(tmp_path: Path) -> None:
    """Replay follows append order (insert id), never the at column.

    set_start_time's payload start (09:55) sorts BEFORE the crossing's
    at (10:02) in the audit at column, so at-ordering would replay it
    first. Append order (id) must win: the events read back in the
    exact order they were appended.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        roster, key = _replay_roster_and_key()
        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={
                    "plate": "12",
                    "entry_id": key,
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:02:00",
                },
            ),
        )
        store.append(
            ride_id,
            Event(
                action="set_start_time",
                payload={
                    "actual_start": "2026-09-20T09:55:00",
                    "previous_start": "2026-09-20T10:00:00",
                },
            ),
        )

        engine = store.load_engine(ride_id, roster)
    finally:
        store.close()

    assert [e.action for e in engine.events] == ["start", "record_crossing", "set_start_time"]
    assert engine.lap_times(key) == (420.0,)


def test_store_load_engine_reconstructs_ride_config_from_stored_columns(
    tmp_path: Path,
) -> None:
    """Every create_ride field round-trips the rebuilt config."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(
            _config(
                name="Round Trip",
                venue="Round Trip Venue",
                lap_km=6.5,
                organizer="Org",
                scorer="Scorer",
                planned_duration_s=7200,
                min_lap_s=30,
                max_team_size=6,
                deck_count=2,
                jokers_per_deck=0,
                jokers_mode=JOKERS_MODE_PER_DECK,
                max_cards=5,
                tiebreak_order=("laps", "high_card", "total_time"),
            )
        )

        engine = store.load_engine(ride_id, _replay_roster())
    finally:
        store.close()

    config = engine.config
    assert config.name == "Round Trip"
    assert config.event_date == date(2026, 9, 20)
    assert config.venue == "Round Trip Venue"
    assert config.lap_km == 6.5
    assert config.organizer == "Org"
    assert config.scorer == "Scorer"
    assert config.planned_start == datetime(2026, 9, 20, 10, 0)  # noqa: DTZ001 -- naive round-trip
    assert config.planned_duration_s == 7200
    assert config.min_lap_s == 30
    assert config.entry_mode is EntryMode.MIXED
    assert config.plate_model is PlateModel.RIDER_POOLED
    assert config.max_team_size == 6
    assert config.deck_count == 2
    assert config.jokers_per_deck == 0
    assert config.jokers_mode == JOKERS_MODE_PER_DECK
    assert config.max_cards == 5
    assert config.tiebreak_order == ("laps", "high_card", "total_time")
    assert config.logo_path is None


def test_store_load_engine_builds_shoe_from_the_stored_rng_seed(tmp_path: Path) -> None:
    """The replay shoe is built from the ride row's own seed."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        roster, key = _replay_roster_and_key()
        store.append(
            ride_id,
            Event(
                action="deal_manual",
                payload={"plate": "12", "entry_id": key, "card": "AS", "reason": "manual"},
            ),
        )

        engine = store.load_engine(ride_id, roster)
    finally:
        store.close()

    assert engine._shoe.dealt == 1  # one card off the fresh replay shoe
    assert engine.config.deck_count == 8
    assert engine.config.jokers_per_deck == DEFAULT_JOKERS_PER_DECK
    # The default mode is total (ride.jokers_mode's own column default),
    # so the fresh shoe holds 8 x 52 + 1 joker.
    assert engine.config.jokers_mode == JOKERS_MODE_TOTAL
    assert engine._shoe.remaining == DEFAULT_DECK_COUNT * 52 + DEFAULT_JOKERS_PER_DECK - 1


# --------------------- load_engine's corrupt-row refusals (T-5)
# ``load_engine`` documents RideNotFoundError for a missing ride and
# StoreError for anything else it cannot rebuild. The ride and audit
# rows it reads are plain columns a hand-edited, truncated or older
# file can hold in any shape, so every rebuild failure names the ride
# (and, for replay, the audit row) rather than leaking a
# JSONDecodeError/KeyError/TypeError/ValueError past the callers that
# catch only RideEngineError/StoreError.


def _audit_row_id(path: Path, ride_id: int) -> int:
    """Return the ride's only audit row's id (arrange/assert aid)."""
    with closing(sqlite3.connect(str(path))) as conn:
        row = conn.execute("SELECT id FROM audit WHERE ride_id = ?", (ride_id,)).fetchone()
    if row is None:
        raise AssertionError(f"no audit row for ride {ride_id}")
    return int(row[0])


def _rewrite_audit_payload(path: Path, ride_id: int, payload_json: str) -> int:
    """Overwrite the ride's audit payload; return the row's id."""
    row_id = _audit_row_id(path, ride_id)
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("UPDATE audit SET payload_json = ? WHERE id = ?", (payload_json, row_id))
        conn.commit()
    return row_id


def _rewrite_tiebreak_order(path: Path, ride_id: int, stored: str) -> None:
    """Overwrite the ride row's stored tiebreak_order JSON text."""
    with closing(sqlite3.connect(str(path))) as conn:
        conn.execute("UPDATE ride SET tiebreak_order = ? WHERE id = ?", (stored, ride_id))
        conn.commit()


def test_store_load_engine_given_undecodable_audit_payload_raises_store_error(
    tmp_path: Path,
) -> None:
    """T-5: payload_json that is not JSON is reported, not leaked."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        row_id = _rewrite_audit_payload(db_path, ride_id, '{"actual_start": ')

        with pytest.raises(StoreError, match=re.escape(f"audit row {row_id}")) as caught:
            store.load_engine(ride_id)
    finally:
        store.close()

    assert f"ride {ride_id}" in str(caught.value)
    assert isinstance(caught.value.__cause__, json.JSONDecodeError)


def test_store_load_engine_given_audit_payload_missing_a_key_raises_store_error(
    tmp_path: Path,
) -> None:
    """T-5: a payload missing its timestamp key names the row."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        row_id = _rewrite_audit_payload(db_path, ride_id, "{}")

        with pytest.raises(StoreError, match=re.escape(f"audit row {row_id}")) as caught:
            store.load_engine(ride_id)
    finally:
        store.close()

    assert f"ride {ride_id}" in str(caught.value)
    assert isinstance(caught.value.__cause__, KeyError)


def test_store_load_engine_given_audit_payload_bad_timestamp_raises_store_error(
    tmp_path: Path,
) -> None:
    """T-5: an unparseable timestamp names the row, not ValueError."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        row_id = _rewrite_audit_payload(db_path, ride_id, '{"actual_start": "yesterday"}')

        with pytest.raises(StoreError, match=re.escape(f"audit row {row_id}")) as caught:
            store.load_engine(ride_id)
    finally:
        store.close()

    assert f"ride {ride_id}" in str(caught.value)
    assert isinstance(caught.value.__cause__, ValueError)


def test_store_load_engine_given_audit_payload_that_is_not_an_object_raises_store_error(
    tmp_path: Path,
) -> None:
    """T-5: a JSON array payload (unsubscriptable) names the row."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        row_id = _rewrite_audit_payload(db_path, ride_id, "[]")

        with pytest.raises(StoreError, match=re.escape(f"audit row {row_id}")) as caught:
            store.load_engine(ride_id)
    finally:
        store.close()

    assert f"ride {ride_id}" in str(caught.value)
    assert isinstance(caught.value.__cause__, TypeError)


@pytest.mark.parametrize(
    "stored",
    [
        "null",  # T-4 nullable: JSON null, never a list
        "not json",  # undecodable text
        '"laps"',  # a JSON scalar, not a list
        "[]",  # T-4 collection: empty
        '["laps"]',  # T-4 collection: single
        '["laps", "high_card"]',  # T-4 min-1: two steps
        '["laps", "high_card", "total_time", "laps"]',  # T-4 max+1: four steps
        '["laps", "high_card", "fastest"]',  # unknown TIEBREAK_* spelling
    ],
    ids=[
        "json_null",
        "not_json",
        "scalar",
        "empty",
        "single",
        "two_steps",
        "four_steps",
        "unknown_step",
    ],
)
def test_store_load_engine_given_an_invalid_tiebreak_order_raises_store_error(
    tmp_path: Path, stored: str
) -> None:
    """T-5: only three known TIEBREAK_* spellings rebuild the config."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        _rewrite_tiebreak_order(db_path, ride_id, stored)

        with pytest.raises(StoreError, match=re.escape(f"ride {ride_id}")) as caught:
            store.load_engine(ride_id)
    finally:
        store.close()

    assert "tiebreak_order" in str(caught.value)
    assert stored in str(caught.value)


# --------------------------------- Phase 5: jokers_mode round trip
# The ride row's own jokers_mode column: the setup dialog's per-deck /
# total radio pair, persisted so a reloaded ride rebuilds the same shoe
# (spec §4/R-40).


def test_store_create_ride_jokers_mode_column_round_trips(tmp_path: Path) -> None:
    """jokers_mode stores its spelling and rebuilds the config field."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        total_id = store.create_ride(_config(name="Total Jokers", jokers_mode=JOKERS_MODE_TOTAL))
        per_deck_id = store.create_ride(
            _config(name="Per Deck Jokers", jokers_mode=JOKERS_MODE_PER_DECK)
        )
    finally:
        store.close()

    assert _fetch_ride_row(db_path, total_id)["jokers_mode"] == "total"
    assert _fetch_ride_row(db_path, per_deck_id)["jokers_mode"] == "per_deck"

    reopened = Store.open(db_path)
    try:
        total_engine = reopened.load_engine(total_id)
        per_deck_engine = reopened.load_engine(per_deck_id)
    finally:
        reopened.close()
    assert total_engine.config.jokers_mode == JOKERS_MODE_TOTAL
    assert per_deck_engine.config.jokers_mode == JOKERS_MODE_PER_DECK


def test_store_load_engine_given_a_row_without_a_jokers_mode_rebuilds_total(
    tmp_path: Path,
) -> None:
    """A row that never set the mode replays as the total default.

    A ``ride`` row written directly -- not through ``create_ride`` --
    still gets ``'total'`` from the column's NOT NULL DEFAULT, so the
    rebuilt config never fabricates a mode the row did not record.
    """
    db_path = tmp_path / "direct.db"
    conn = sqlite3.connect(str(db_path))
    try:
        for statement in (*SCHEMA_STATEMENTS, SCHEMA_VERSION_DDL):
            conn.execute(statement)
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, ?)", (SCHEMA_VERSION,))
        conn.execute(
            """
            INSERT INTO ride (
                name, event_date, venue, course_name, lap_km, organizer, scorer,
                logo_png, planned_start, planned_duration_s, actual_start,
                finished_at, status, entry_mode, max_team_size, plate_model,
                min_lap_s, deck_count, jokers_per_deck, max_cards, tiebreak_order,
                rng_seed, created_at, updated_at
            ) VALUES (
                'Club night', '2026-09-20', 'Gondola', 'Gondola', 8.0,
                'GORBA', 'K. Singh', NULL, 1789898400, 21600, NULL, NULL,
                'draft', 'mixed', 4, 'rider_pooled', 1080, 8, 2, NULL,
                '["laps","total_time","high_card"]', 20260920, 1789898400, 1789898400
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    store = Store.open(db_path)
    try:
        ride_id = store.rides()[0].id
        engine = store.load_engine(ride_id)
    finally:
        store.close()

    assert engine.config.jokers_mode == JOKERS_MODE_TOTAL


@pytest.mark.parametrize(
    ("jokers_mode", "expected_jokers"),
    [(JOKERS_MODE_TOTAL, 2), (JOKERS_MODE_PER_DECK, 4)],
    ids=["total", "per_deck"],
)
def test_store_load_engine_builds_the_shoe_from_the_stored_jokers_mode(
    tmp_path: Path, jokers_mode: str, expected_jokers: int
) -> None:
    """The reloaded shoe spends its jokers the way the row recorded."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(
            _config(deck_count=2, jokers_per_deck=2, jokers_mode=jokers_mode)
        )
        engine = store.load_engine(ride_id)
    finally:
        store.close()

    dealt = _deal_all(engine._shoe)

    assert sum(1 for card in dealt if card.joker) == expected_jokers


def test_store_duplicate_ride_copies_jokers_mode(tmp_path: Path) -> None:
    """R-15: the copy keeps the source's own jokers mode."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        source_id = store.create_ride(_config(jokers_mode=JOKERS_MODE_PER_DECK))
        copy_id = store.duplicate_ride(source_id)
    finally:
        store.close()

    assert _fetch_ride_row(db_path, copy_id)["jokers_mode"] == "per_deck"


def test_store_update_ride_config_rewrites_jokers_mode(tmp_path: Path) -> None:
    """Edit Ride: a mode change lands in the row and the config."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(jokers_mode=JOKERS_MODE_TOTAL))
        store.update_ride_config(ride_id, _config(jokers_mode=JOKERS_MODE_PER_DECK))
    finally:
        store.close()

    assert _fetch_ride_row(db_path, ride_id)["jokers_mode"] == "per_deck"

    reopened = Store.open(db_path)
    try:
        assert reopened.load_engine(ride_id).config.jokers_mode == JOKERS_MODE_PER_DECK
    finally:
        reopened.close()


# --------------------------------------- E5.2.1 session bookkeeping


def _fetch_latest_session(path: Path) -> dict[str, object]:
    """Read the newest app_session row back out of the file."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT id, opened_at, closed_at, active_ride_id"
            " FROM app_session ORDER BY id DESC LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        raise AssertionError("no app_session row")
    return dict(row)


def _created_ride_id(path: Path) -> int:
    """Create one ride and return its id (session arrange helper)."""
    store = Store.open(path)
    try:
        ride_id = store.create_ride(_config(name="Session Ride"))
    finally:
        store.close()
    return ride_id


def test_store_open_inserts_session_row_with_opened_at_and_null_close(
    tmp_path: Path,
) -> None:
    """Every open records one session: opened_at now, closed_at NULL."""
    db_path = tmp_path / "rides.db"

    Store.open(db_path).close()

    row = _fetch_latest_session(db_path)
    assert isinstance(row["opened_at"], int)
    assert row["opened_at"] > 0
    assert row["closed_at"] is None
    assert row["active_ride_id"] is None


def test_store_open_with_active_ride_id_records_the_running_ride(
    tmp_path: Path,
) -> None:
    """A running ride's id lands in active_ride_id (new session)."""
    db_path = tmp_path / "rides.db"
    ride_id = _created_ride_id(db_path)

    Store.open(db_path, active_ride_id=ride_id).close()

    row = _fetch_latest_session(db_path)
    assert row["active_ride_id"] == ride_id
    assert row["closed_at"] is None


def test_store_open_with_unknown_active_ride_refuses_loudly(tmp_path: Path) -> None:
    """A missing active_ride_id fails the FK, never a silent NULL."""
    db_path = tmp_path / "rides.db"
    Store.open(db_path).close()

    with pytest.raises(sqlite3.IntegrityError, match=re.escape("FOREIGN KEY")):
        Store.open(db_path, active_ride_id=999)


def test_store_close_session_writes_closed_at_on_the_open_session(
    tmp_path: Path,
) -> None:
    """close_session stamps the open session with closed_at."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        store.close_session()
    finally:
        store.close()

    row = _fetch_latest_session(db_path)
    assert isinstance(row["closed_at"], int)
    assert row["closed_at"] >= row["opened_at"]


def test_store_close_session_with_no_session_row_is_a_noop(tmp_path: Path) -> None:
    """close_session on an empty table raises nothing, changes none."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        store._conn.execute("DELETE FROM app_session")

        result = store.close_session()
    finally:
        store.close()

    assert result is None
    with closing(sqlite3.connect(str(db_path))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM app_session").fetchone()[0] == 0


def test_store_session_state_fresh_database_returns_clean_quit(tmp_path: Path) -> None:
    """A first launch (no prior session) reads CLEAN_QUIT, not crash."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        state = store.session_state()
    finally:
        store.close()

    assert state is SessionState.CLEAN_QUIT


def test_store_session_state_clean_quit_reads_previous_session(tmp_path: Path) -> None:
    """After a clean close + reopen: CLEAN_QUIT, never the open row."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    store.close_session()
    store.close()

    reopened = Store.open(db_path)
    try:
        state = reopened.session_state()
    finally:
        reopened.close()

    assert state is SessionState.CLEAN_QUIT


def test_store_session_state_crashed_reads_previous_session(tmp_path: Path) -> None:
    """A prior session left open (no close_session) reads CRASHED."""
    db_path = tmp_path / "rides.db"
    Store.open(db_path).close()  # no close_session -- the crash

    reopened = Store.open(db_path)
    try:
        state = reopened.session_state()
    finally:
        reopened.close()

    assert state is SessionState.CRASHED


def test_store_session_state_running_at_exit_reads_previous_session(
    tmp_path: Path,
) -> None:
    """Closed cleanly with active_ride_id set reads RUNNING_AT_EXIT."""
    db_path = tmp_path / "rides.db"
    ride_id = _created_ride_id(db_path)
    store = Store.open(db_path, active_ride_id=ride_id)
    store.close_session()
    store.close()

    reopened = Store.open(db_path)
    try:
        state = reopened.session_state()
    finally:
        reopened.close()

    assert state is SessionState.RUNNING_AT_EXIT


def test_store_session_state_crash_with_running_ride_reads_crashed(
    tmp_path: Path,
) -> None:
    """A crash while a ride ran still reads CRASHED, not running."""
    db_path = tmp_path / "rides.db"
    ride_id = _created_ride_id(db_path)
    Store.open(db_path, active_ride_id=ride_id).close()  # crash, no close_session

    reopened = Store.open(db_path)
    try:
        state = reopened.session_state()
    finally:
        reopened.close()

    assert state is SessionState.CRASHED


def test_store_session_state_reads_second_newest_not_an_older_one(
    tmp_path: Path,
) -> None:
    """The reading is the session the current open supersedes."""
    db_path = tmp_path / "rides.db"
    Store.open(db_path).close()  # session A: crash (never closed)
    ride_id = _created_ride_id(db_path)
    store = Store.open(db_path, active_ride_id=ride_id)  # session B
    store.close_session()  # B closed cleanly, running ride at exit
    store.close()

    reopened = Store.open(db_path)  # session C: the current open
    try:
        state = reopened.session_state()
    finally:
        reopened.close()

    # The previous session is B (second-newest), so RUNNING_AT_EXIT --
    # not A's CRASHED, which session C did not supersede.
    assert state is SessionState.RUNNING_AT_EXIT


@pytest.mark.parametrize(
    ("member", "expected_value"),
    [
        (SessionState.CLEAN_QUIT, "clean_quit"),
        (SessionState.CRASHED, "crashed"),
        (SessionState.RUNNING_AT_EXIT, "running_at_exit"),
    ],
)
def test_store_session_state_members_have_stable_serialized_values(
    member: SessionState, expected_value: str
) -> None:
    """The enum spellings stay stable for any stored form."""
    assert member.value == expected_value


# --------------------------------------- E5.2.2 resume reading


def _session_row(path: Path) -> sqlite3.Row:
    """Read the newest app_session row, or the second-newest if asked.

    The same reading ``Store.session_state`` uses (second-newest is
    the previous session); assertions here compare the facade's
    :meth:`Store.previous_session` against the raw row it wraps.
    """
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(
            "SELECT closed_at, active_ride_id, heartbeat_at, opened_at"
            " FROM app_session ORDER BY id DESC LIMIT 1 OFFSET 1"
        ).fetchone()
    finally:
        conn.close()


def test_store_previous_session_fresh_database_returns_clean_quit(tmp_path: Path) -> None:
    """A first launch (no prior session) reads CLEAN_QUIT, no ride."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        previous = store.previous_session()
    finally:
        store.close()

    assert previous.state is SessionState.CLEAN_QUIT
    assert previous.ride_id is None
    assert previous.ended_at is None


def test_store_previous_session_running_at_exit_carries_ride_id_and_closed_at(
    tmp_path: Path,
) -> None:
    """Quit-keep-running: the reading carries the ride and quit time."""
    db_path = tmp_path / "rides.db"
    ride_id = _created_ride_id(db_path)
    store = Store.open(db_path, active_ride_id=ride_id)
    store.close_session()
    store.close()

    reopened = Store.open(db_path)
    try:
        previous = reopened.previous_session()
    finally:
        reopened.close()
    row = _session_row(db_path)
    assert row["closed_at"] is not None

    assert previous.state is SessionState.RUNNING_AT_EXIT
    assert previous.ride_id == ride_id
    # naive local, _from_epoch's inverse
    assert previous.ended_at == datetime.fromtimestamp(  # noqa: DTZ006
        row["closed_at"]
    )


def test_store_previous_session_crashed_without_heartbeat_uses_opened_at(
    tmp_path: Path,
) -> None:
    """No heartbeat written: the crash copy falls back to opened_at."""
    db_path = tmp_path / "rides.db"
    ride_id = _created_ride_id(db_path)
    Store.open(db_path, active_ride_id=ride_id).close()  # the crash
    reopened = Store.open(db_path)
    try:
        previous = reopened.previous_session()
    finally:
        reopened.close()
    # Read the previous-session row AFTER the second open so OFFSET 1
    # is the crash session, not the arrange helper's session -- reading
    # earlier compared the wrong row's opened_at and flaked across a
    # second boundary on a slow Windows runner (the sibling tests read
    # after the second open for the same reason).
    row = _session_row(db_path)
    assert row["heartbeat_at"] is None

    assert previous.state is SessionState.CRASHED
    assert previous.ride_id == ride_id
    # naive local, _from_epoch's inverse
    assert previous.ended_at == datetime.fromtimestamp(  # noqa: DTZ006
        row["opened_at"]
    )


def test_store_previous_session_crashed_with_heartbeat_uses_last_heartbeat(
    tmp_path: Path,
) -> None:
    """A written heartbeat is the crash copy's time (spec §3)."""
    db_path = tmp_path / "rides.db"
    ride_id = _created_ride_id(db_path)
    store = Store.open(db_path, active_ride_id=ride_id)
    heartbeat_epoch = int(
        # local epoch, what the store writes
        datetime(2026, 9, 20, 12, 37).timestamp()  # noqa: DTZ001
    )
    with store._conn:
        store._conn.execute(
            "UPDATE app_session SET heartbeat_at = ?"
            " WHERE id = (SELECT id FROM app_session ORDER BY id DESC LIMIT 1)",
            (heartbeat_epoch,),
        )
    store.close()  # no close_session -- the crash

    reopened = Store.open(db_path)
    try:
        previous = reopened.previous_session()
    finally:
        reopened.close()

    assert previous.state is SessionState.CRASHED
    # naive local, _from_epoch's inverse
    assert previous.ended_at == datetime.fromtimestamp(  # noqa: DTZ006
        heartbeat_epoch
    )


def test_store_previous_session_clean_quit_carries_no_ride(tmp_path: Path) -> None:
    """A clean quit with no running ride reads CLEAN_QUIT, ride None."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    store.close_session()
    store.close()

    reopened = Store.open(db_path)
    try:
        previous = reopened.previous_session()
    finally:
        reopened.close()

    assert previous.state is SessionState.CLEAN_QUIT
    assert previous.ride_id is None


def test_store_set_active_ride_marks_the_open_session(tmp_path: Path) -> None:
    """Continue marks the current session's running ride (R-52)."""
    db_path = tmp_path / "rides.db"
    ride_id = _created_ride_id(db_path)
    store = Store.open(db_path)  # the launch open, ride unknown yet
    try:
        store.set_active_ride(ride_id)
    finally:
        store.close()

    row = _fetch_latest_session(db_path)
    assert row["active_ride_id"] == ride_id


def test_store_clear_active_ride_clears_the_open_sessions_ride(tmp_path: Path) -> None:
    """Finish / launch-failure NULLs the current session's ride (W3)."""
    db_path = tmp_path / "rides.db"
    ride_id = _created_ride_id(db_path)
    store = Store.open(db_path)
    try:
        store.set_active_ride(ride_id)

        store.clear_active_ride()
    finally:
        store.close()

    row = _fetch_latest_session(db_path)
    assert row["active_ride_id"] is None


def test_store_clear_active_ride_with_no_session_row_is_a_noop(tmp_path: Path) -> None:
    """An empty app_session table updates nothing and raises nothing."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        with store._conn:
            store._conn.execute("DELETE FROM app_session")

        store.clear_active_ride()
    finally:
        store.close()

    conn = sqlite3.connect(str(db_path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM app_session").fetchone()[0]
    finally:
        conn.close()
    assert count == 0


def test_store_roster_for_returns_empty_roster_with_the_rides_shape(tmp_path: Path) -> None:
    """load_engine's roster shell carries the ride's mode/team size."""
    db_path = tmp_path / "rides.db"
    ride_id = _created_ride_id(db_path)
    store = Store.open(db_path)
    try:
        roster = store.roster_for(ride_id)
    finally:
        store.close()

    assert (roster.entry_mode, roster.plate_model, roster.max_team_size) == (
        EntryMode.MIXED,
        PlateModel.RIDER_POOLED,
        4,
    )
    assert roster.entries == ()


def test_store_roster_for_unknown_ride_raises_naming_the_id(tmp_path: Path) -> None:
    """T-5: roster_for's negative case names the missing ride."""
    db_path = tmp_path / "rides.db"
    Store.open(db_path).close()

    store = Store.open(db_path)
    try:
        with pytest.raises(RideNotFoundError, match=re.escape("no ride with id 999")):
            store.roster_for(999)
    finally:
        store.close()


# ------------------------------------------- E5.3.2 delete guard (R-18)


def _backup_files(db_path: Path) -> list[Path]:
    """Return *db_path*'s backup files, newest first by name."""
    directory = backup.backup_dir_for(db_path)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.db"), reverse=True)


def _mark_running(path: Path, ride_id: int) -> None:
    """Set one ride's stored status to RUNNING (arrange, R-18).

    ``create_ride`` writes ``draft``; today the engine's ``start``
    event lands in the audit log without the facade syncing the
    ``ride`` row (E5.4's engine-sync writes it). The delete guard
    reads the stored column -- the persisted truth the library shows
    -- so the arrange writes the column directly, the same real-SQL
    pattern the session-heartbeat tests already use.
    """
    store = Store.open(path)
    try:
        with store._conn:
            store._conn.execute("UPDATE ride SET status = 'running' WHERE id = ?", (ride_id,))
    finally:
        store.close()


def test_store_delete_ride_writes_backup_then_removes_ride(tmp_path: Path) -> None:
    """R-18: delete writes a backup first, then removes the ride."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Club poker night"))
        store.delete_ride(ride_id, "Club poker night")
        assert store.rides() == []
    finally:
        store.close()

    files = _backup_files(db_path)
    assert len(files) == 1
    reopened = Store.open(files[0])
    try:
        assert [ride.name for ride in reopened.rides()] == ["Club poker night"]
    finally:
        reopened.close()


def test_store_delete_ride_backup_reopens_with_integrity_ok(tmp_path: Path) -> None:
    """The backup written before the delete is a valid database."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Integrity"))
        store.delete_ride(ride_id, "Integrity")
    finally:
        store.close()

    files = _backup_files(db_path)
    assert len(files) == 1
    reopened = Store.open(files[0])
    try:
        assert reopened.rides()[0].name == "Integrity"
    finally:
        reopened.close()
    with closing(sqlite3.connect(str(files[0]))) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_store_delete_ride_typed_name_mismatch_raises_naming_the_ride(
    tmp_path: Path,
) -> None:
    """R-18: a near-miss name is refused -- no case-fold, no strip."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Club poker night"))
        with pytest.raises(
            RideNameMismatchError,
            match=re.escape("does not match ride"),
        ):
            store.delete_ride(ride_id, "club poker night")
        assert len(store.rides()) == 1  # nothing deleted
    finally:
        store.close()

    assert _backup_files(db_path) == []  # validation runs before the backup


def test_store_delete_ride_typed_name_empty_raises(tmp_path: Path) -> None:
    """T-4: an empty typed name is a mismatch, never a delete."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Club poker night"))
        with pytest.raises(RideNameMismatchError, match=re.escape("does not match")):
            store.delete_ride(ride_id, "")
    finally:
        store.close()


def test_store_delete_ride_running_ride_refuses_naming_it(tmp_path: Path) -> None:
    """R-18: a RUNNING ride is never deletable."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Live"))
    finally:
        store.close()
    _mark_running(db_path, ride_id)

    store = Store.open(db_path)
    try:
        with pytest.raises(
            RideRunningError,
            match=re.escape("is RUNNING and cannot be deleted"),
        ):
            store.delete_ride(ride_id, "Live")
        assert [ride.name for ride in store.rides()] == ["Live"]
    finally:
        store.close()

    assert _backup_files(db_path) == []  # the refusal precedes the backup


def test_store_delete_ride_unknown_ride_raises_naming_it(tmp_path: Path) -> None:
    """T-5: deleting a ride id that never existed fails loudly."""
    db_path = tmp_path / "rides.db"
    Store.open(db_path).close()

    store = Store.open(db_path)
    try:
        with pytest.raises(RideNotFoundError, match=re.escape("no ride with id 999")):
            store.delete_ride(999, "any name")
    finally:
        store.close()


def test_store_delete_ride_removes_all_dependent_rows(tmp_path: Path) -> None:
    """Deleting a ride removes its entries/riders/crossings/cards/audit.

    The schema declares plain ``REFERENCES`` (no ON DELETE CASCADE --
    recorded decision), so delete_ride must remove the dependents
    itself, in FK-safe order, in one transaction. The audit row is a
    crossing event: a start would sync the row to RUNNING and trip the
    delete guard, and this test is about dependents, not lifecycle.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Dependents"))
        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={
                    "plate": "12",
                    "entry_id": "12",
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:02:00",
                },
            ),
        )
        with store._conn:
            entry_id = store._conn.execute(
                "INSERT INTO entry"
                " (ride_id, plate, key, display_name, type, team_size, status)"
                " VALUES (?, '12', '2f7a', 'Alice', 'solo', 1, 'active')",
                (ride_id,),
            ).lastrowid
            rider_id = store._conn.execute(
                "INSERT INTO rider (entry_id, first_name, last_name, plate, sort_order)"
                " VALUES (?, 'Alice', '', '12', 1)",
                (entry_id,),
            ).lastrowid
            crossing_id = store._conn.execute(
                "INSERT INTO crossing (ride_id, entry_id, rider_id, seq, crossed_at, lap_s, flag)"
                " VALUES (?, ?, ?, 1, 100, 50, 'none')",
                (ride_id, entry_id, rider_id),
            ).lastrowid
            store._conn.execute(
                "INSERT INTO card"
                " (ride_id, entry_id, crossing_id, shoe_index, rank, suit, state, dealt_at)"
                " VALUES (?, ?, ?, 0, 14, 's', 'dealt', 100)",
                (ride_id, entry_id, crossing_id),
            )
            store._conn.execute(
                "UPDATE app_session SET active_ride_id = ?"
                " WHERE id = (SELECT id FROM app_session ORDER BY id DESC LIMIT 1)",
                (ride_id,),
            )
        # lastrowid is int | None; the three ids feed the DELETE
        # assertions below, so narrow them with isinstance (a type
        # guard, never the test's own final assertion -- T-2).
        assert isinstance(entry_id, int)
        assert isinstance(rider_id, int)
        assert isinstance(crossing_id, int)

        store.delete_ride(ride_id, "Dependents")
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        # rider has no ride_id column -- it links through entry (the
        # schema's own shape; delete_ride removes it via a subquery).
        rider_count = conn.execute(
            "SELECT COUNT(*) FROM rider WHERE entry_id IN"
            " (SELECT id FROM entry WHERE ride_id = ?)",
            (ride_id,),
        ).fetchone()[0]
        assert rider_count == 0, "rider rows survived the delete"
        entry_count = conn.execute(
            "SELECT COUNT(*) FROM entry WHERE ride_id = ?", (ride_id,)
        ).fetchone()[0]
        assert entry_count == 0, "entry rows survived the delete"
        crossing_count = conn.execute(
            "SELECT COUNT(*) FROM crossing WHERE ride_id = ?", (ride_id,)
        ).fetchone()[0]
        assert crossing_count == 0, "crossing rows survived the delete"
        card_count = conn.execute(
            "SELECT COUNT(*) FROM card WHERE ride_id = ?", (ride_id,)
        ).fetchone()[0]
        assert card_count == 0, "card rows survived the delete"
        audit_count = conn.execute(
            "SELECT COUNT(*) FROM audit WHERE ride_id = ?", (ride_id,)
        ).fetchone()[0]
        assert audit_count == 0, "audit rows survived the delete"
        session_row = conn.execute(
            "SELECT active_ride_id FROM app_session ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert session_row[0] is None  # the session's ride reference is cleared
        assert conn.execute("SELECT COUNT(*) FROM ride").fetchone()[0] == 0


def test_store_delete_ride_keeps_other_rides_untouched(tmp_path: Path) -> None:
    """Deleting one ride never touches a sibling ride's rows."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        doomed = store.create_ride(_config(name="Doomed"))
        store.create_ride(_config(name="Kept"))
        store.delete_ride(doomed, "Doomed")
    finally:
        store.close()

    reopened = Store.open(db_path)
    try:
        assert [ride.name for ride in reopened.rides()] == ["Kept"]
    finally:
        reopened.close()


def test_store_delete_ride_error_types_are_store_errors() -> None:
    """Both new guards surface as StoreError subclasses (T-12)."""
    assert issubclass(RideNameMismatchError, StoreError)
    assert issubclass(RideRunningError, StoreError)


# --------------------------------------- E5.4.1 roster persistence


def _solo_roster() -> Roster:
    """Build a solo-only rider_pooled roster for round-trip tests."""
    roster = Roster(entry_mode=EntryMode.SOLO, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate="12")
    return roster


def _pooled_roster() -> Roster:
    """Build the MIXED rider_pooled roster E5.4.1 round-trips.

    One solo entry plus one team of two riders, each carrying their
    own plate -- the team's derived plate is the lowest ("77").
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate="12")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    return roster


def _relay_roster() -> Roster:
    """Build the MIXED team_relay roster E5.4.1 round-trips.

    The team carries one plate ("88"); its riders are plateless
    (S1 -- the plate belongs to the entry, not the individual).
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_solo_entry(first_name="Alice", last_name="", plate="12")
    roster.create_team_entry(
        display_name="Moss Ridge",
        riders=[
            Rider(first_name="R.", last_name="Dubois"),
            Rider(first_name="M.", last_name="Chen"),
        ],
        plate="88",
    )
    return roster


def _save_roster_ride(path: Path, roster: Roster, **config_overrides: object) -> int:
    """Create a ride whose config matches *roster* (arrange).

    The ride row's entry_mode/plate_model must agree with *roster*'s
    own settings -- ``_load_roster`` rebuilds the shell from those
    stored columns.
    """
    store = Store.open(path)
    try:
        ride_id = store.create_ride(_config(**config_overrides))
        store.save_roster(ride_id, roster)
    finally:
        store.close()
    return ride_id


def _round_trip_roster(path: Path, ride_id: int) -> Roster:
    """Reopen the store and reconstruct *ride_id*'s roster (arrange)."""
    store = Store.open(path)
    try:
        return store.roster_for(ride_id)
    finally:
        store.close()


def test_store_save_roster_solo_round_trips_entry_and_rider(tmp_path: Path) -> None:
    """A solo entry's plate/name/type and rider survive a round-trip."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _solo_roster(),
        entry_mode=EntryMode.SOLO,
        plate_model=PlateModel.RIDER_POOLED,
    )

    roster = _round_trip_roster(db_path, ride_id)

    assert (roster.entry_mode, roster.plate_model, roster.max_team_size) == (
        EntryMode.SOLO,
        PlateModel.RIDER_POOLED,
        4,
    )
    (entry,) = roster.entries
    assert (entry.plate, entry.display_name, entry.type.value) == ("12", "Alice", "solo")
    assert entry.team_size == 1
    (rider,) = entry.riders
    assert (rider.first_name, rider.last_name, rider.full_name) == ("Alice", "", "Alice")
    assert (rider.plate, rider.sort_order) == ("12", 0)


def test_store_save_roster_writes_first_and_last_name_columns(tmp_path: Path) -> None:
    """The rider table stores the split, not a single name column."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute(
            "SELECT first_name, last_name FROM rider"
            " WHERE entry_id IN (SELECT id FROM entry WHERE ride_id = ?) ORDER BY id",
            (ride_id,),
        ).fetchall()

    assert rows == [("Alice", ""), ("A.", "Roy"), ("K.", "Singh")]


def test_store_save_roster_writes_the_canonical_sex_letters(tmp_path: Path) -> None:
    """The rider table stores "M"/"F" and NULL for an unknown sex."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate="12", sex="F")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77", sex="M"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        roster,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute(
            "SELECT sex FROM rider"
            " WHERE entry_id IN (SELECT id FROM entry WHERE ride_id = ?) ORDER BY id",
            (ride_id,),
        ).fetchall()

    assert rows == [("F",), ("M",), (None,)]


@pytest.mark.parametrize("sex", ["M", "F", None])
def test_store_save_roster_round_trips_rider_sex(tmp_path: Path, sex: str | None) -> None:
    """Rider.sex survives save_roster -> roster_for."""
    roster = Roster(entry_mode=EntryMode.SOLO, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate="12", sex=sex)
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        roster,
        entry_mode=EntryMode.SOLO,
        plate_model=PlateModel.RIDER_POOLED,
    )

    rebuilt = _round_trip_roster(db_path, ride_id)

    (entry,) = rebuilt.entries
    (rider,) = entry.riders
    assert rider.sex == sex


def test_store_duplicate_ride_copies_rider_sex(tmp_path: Path) -> None:
    """R-15's roster copy carries each rider's stored sex."""
    db_path = tmp_path / "rides.db"
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate="12", sex="F")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="77", sex="M"),
            Rider(first_name="K.", last_name="Singh", plate="78"),
        ],
    )
    source_ride = _save_roster_ride(
        db_path,
        roster,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    store = Store.open(db_path)
    try:
        copy_ride = store.duplicate_ride(source_ride)
        copied = store.roster_for(copy_ride)
    finally:
        store.close()

    assert [
        (rider.full_name, rider.sex) for entry in copied.entries for rider in entry.riders
    ] == [
        ("Alice", "F"),
        ("A. Roy", "M"),
        ("K. Singh", None),
    ]


def test_store_save_roster_round_trips_two_part_rider_names(tmp_path: Path) -> None:
    """A team's two-part rider names survive as first/last/full."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    roster = _round_trip_roster(db_path, ride_id)

    assert [
        (rider.first_name, rider.last_name, rider.full_name) for rider in roster.entries[1].riders
    ] == [
        ("A.", "Roy", "A. Roy"),
        ("K.", "Singh", "K. Singh"),
    ]


def test_store_save_roster_writes_a_null_logo_card_on_every_entry(tmp_path: Path) -> None:
    """Entries with no logo still store NULL in the logo column.

    Phase 4 added the real write path (``test_store_save_roster_
    round_trips_a_teams_logo_card``); this pins the other half -- an
    unseeded in-memory roster carries no logo, and its rows must stay
    NULL, never an empty string.
    """
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute("SELECT logo_card FROM entry WHERE ride_id = ?", (ride_id,)).fetchall()

    assert rows == [(None,), (None,)]


def test_store_save_roster_pooled_team_round_trips_rider_plates(
    tmp_path: Path,
) -> None:
    """A rider_pooled team's members and plates survive a round-trip."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    roster = _round_trip_roster(db_path, ride_id)

    assert len(roster.entries) == 2
    solo, team = roster.entries
    assert (solo.plate, solo.display_name, solo.type.value) == ("12", "Alice", "solo")
    assert (team.plate, team.display_name, team.type.value) == (
        "77",
        "Trail Blazers",
        "team",
    )
    assert [(rider.full_name, rider.plate) for rider in team.riders] == [
        ("A. Roy", "77"),
        ("K. Singh", "78"),
    ]


def test_store_save_roster_relay_team_round_trips_plateless_riders(
    tmp_path: Path,
) -> None:
    """A team_relay entry keeps its plate; its riders stay plateless."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _relay_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.TEAM_RELAY,
    )

    roster = _round_trip_roster(db_path, ride_id)

    assert len(roster.entries) == 2
    solo, team = roster.entries
    assert (solo.plate, team.plate) == ("12", "88")
    assert [rider.plate for rider in team.riders] == [None, None]
    assert [rider.full_name for rider in team.riders] == ["R. Dubois", "M. Chen"]


def test_store_save_roster_entry_notes_round_trip(tmp_path: Path) -> None:
    """Entry notes persist and reconstruct (schema entry.notes)."""
    roster = _pooled_roster()
    roster.update_entry(roster.entries[1], notes="Captain's team")
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        roster,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    rebuilt = _round_trip_roster(db_path, ride_id)

    assert [entry.notes for entry in rebuilt.entries] == ["", "Captain's team"]


def test_store_save_roster_round_trips_each_entry_key(tmp_path: Path) -> None:
    """Each entry's stable key survives save_roster -> roster_for.

    The engine files crossings and credited hands under ``Entry.key``
    (E3.1.2's pooled-live-move seam), so a reloaded ride's replay can
    only resolve them if the key came back from the entry table
    unchanged.
    """
    db_path = tmp_path / "rides.db"
    roster = _pooled_roster()
    ride_id = _save_roster_ride(
        db_path,
        roster,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    rebuilt = _round_trip_roster(db_path, ride_id)

    assert [entry.key for entry in rebuilt.entries] == [entry.key for entry in roster.entries]


def test_store_save_roster_writes_a_distinct_key_per_entry_row(tmp_path: Path) -> None:
    """No two persisted entries share a key."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    rebuilt = _round_trip_roster(db_path, ride_id)

    keys = [entry.key for entry in rebuilt.entries]
    assert len(set(keys)) == len(keys)


def test_store_save_roster_replaces_the_previous_roster(tmp_path: Path) -> None:
    """Saving twice keeps one entry set -- the second, not a union."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    store = Store.open(db_path)
    try:
        store.save_roster(ride_id, _solo_roster())
    finally:
        store.close()

    rebuilt = _round_trip_roster(db_path, ride_id)

    assert len(rebuilt.entries) == 1
    (entry,) = rebuilt.entries
    assert entry.plate == "12"


def test_store_roster_for_derives_has_data_from_crossing_rows(
    tmp_path: Path,
) -> None:
    """has_data is derived from recorded rows, never stored (E5.4.1).

    The schema has no has_data column -- R-15's permanent delete guard
    derives from whether the entry has crossings/cards. After one
    crossing row lands, the reconstructed entry carries has_data.
    """
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    store = Store.open(db_path)
    try:
        with store._conn:
            entry_id = store._conn.execute(
                "SELECT id FROM entry WHERE ride_id = ? AND plate = '12'",
                (ride_id,),
            ).fetchone()[0]
            store._conn.execute(
                "INSERT INTO crossing (ride_id, entry_id, seq, crossed_at, lap_s, flag)"
                " VALUES (?, ?, 1, 100, 50, 'none')",
                (ride_id, entry_id),
            )
        rebuilt = store.roster_for(ride_id)
    finally:
        store.close()

    assert [entry.has_data for entry in rebuilt.entries] == [True, False]


def test_store_load_engine_builds_roster_from_db_and_replays_events(
    tmp_path: Path,
) -> None:
    """load_engine with no caller roster rebuilds it from the DB.

    E5.1.2's equivalence, closed: the engine replays the persisted
    start + crossing, resolving the event's ``entry_id`` -- the stable
    key ``save_roster`` wrote into the entry table -- through the
    reconstructed roster. With an empty roster the replay would be
    refused (unknown entry key) and the crossing never recorded, so
    this genuinely proves the roster, keys included, came back from the
    entry/rider tables.
    """
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        min_lap_s=1,
    )
    store = Store.open(db_path)
    try:
        key = entry_key(store.roster_for(ride_id), "12")
        store.append(
            ride_id,
            Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
        )
        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={
                    "plate": "12",
                    "entry_id": key,
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:02:00",
                },
            ),
        )

        engine = store.load_engine(ride_id)
    finally:
        store.close()

    assert engine.state is RideStatus.RUNNING
    assert [crossing.entry_id for crossing in engine.crossings] == [key]
    assert engine.lap_times(key) == (120.0,)


def test_store_load_engine_replays_short_lap_holds_when_policy_is_stored_true(
    tmp_path: Path,
) -> None:
    """The stored policy column drives replay of held/confirmed cards.

    W4's round-trip: a ride created with ``hold_short_laps=True`` whose
    log holds and then confirms a short-lap card must rebuild to the
    same credited state -- the replayed ``record_crossing`` holds only
    because the reconstructed config carries the stored 1.
    """
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        hold_short_laps=True,
    )
    store = Store.open(db_path)
    try:
        key = entry_key(store.roster_for(ride_id), "12")
        store.append(
            ride_id,
            Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
        )
        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={
                    "plate": "12",
                    "entry_id": key,
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:00:30",
                },
            ),
        )
        card = store.load_engine(ride_id).held_crossings()[0].card
        store.append(
            ride_id,
            Event(
                action="confirm_held",
                payload={"entry_id": key, "seq": 1, "card": card.code()},
            ),
        )

        engine = store.load_engine(ride_id)
    finally:
        store.close()

    assert engine.config.hold_short_laps is True
    assert engine.held_crossings() == ()
    results = {entry.plate: entry for entry in engine.snapshot()}
    assert results["12"].cards == (card,)


def _source_ride_with_timing_data(path: Path, roster: Roster) -> int:
    """Create a ride with a saved roster and timing events (arrange).

    The timing data (a start event and one crossing) is what
    ``duplicate_ride`` must NOT copy.
    """
    store = Store.open(path)
    try:
        ride_id = store.create_ride(_config(name="GORBA EPIC 2026", min_lap_s=1))
        store.save_roster(ride_id, roster)
        store.append(
            ride_id,
            Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
        )
        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={
                    "plate": "12",
                    "entry_id": entry_key(roster, "12"),
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:02:00",
                },
            ),
        )
    finally:
        store.close()
    return ride_id


def test_store_duplicate_ride_copies_setup_and_roster_without_timing_data(
    tmp_path: Path,
) -> None:
    """R-15: the copy is a new DRAFT ride with the roster, no timing.

    The source carries a start event, so its persisted status reads
    RUNNING (the append lifecycle sync); the copy never inherits it.
    """
    db_path = tmp_path / "rides.db"
    source_id = _source_ride_with_timing_data(db_path, _pooled_roster())
    store = Store.open(db_path)
    try:
        copy_id = store.duplicate_ride(source_id)
        rows = store.rides()
        copied = store.roster_for(copy_id)
    finally:
        store.close()

    assert copy_id != source_id
    assert [row.id for row in rows] == [source_id, copy_id]
    assert [row.status for row in rows] == [RideStatus.RUNNING, RideStatus.DRAFT]
    assert rows[1].name == "GORBA EPIC 2026 (copy)"
    assert len(copied.entries) == 2
    assert [(entry.plate, entry.display_name) for entry in copied.entries] == [
        ("12", "Alice"),
        ("77", "Trail Blazers"),
    ]
    with closing(sqlite3.connect(str(db_path))) as conn:
        assert (
            conn.execute("SELECT COUNT(*) FROM crossing WHERE ride_id = ?", (copy_id,)).fetchone()[
                0
            ]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM card WHERE ride_id = ?", (copy_id,)).fetchone()[0]
            == 0
        )
        assert (
            conn.execute("SELECT COUNT(*) FROM audit WHERE ride_id = ?", (copy_id,)).fetchone()[0]
            == 0
        )


def test_store_duplicate_ride_uses_a_fresh_seed_and_never_timing_fields(
    tmp_path: Path,
) -> None:
    """The copy's seed is fresh; actual_start/finished_at stay NULL."""
    db_path = tmp_path / "rides.db"
    source_id = _source_ride_with_timing_data(db_path, _solo_roster())
    store = Store.open(db_path)
    try:
        copy_id = store.duplicate_ride(source_id)
    finally:
        store.close()

    source_row = _fetch_ride_row(db_path, source_id)
    copy_row = _fetch_ride_row(db_path, copy_id)
    assert copy_row["rng_seed"] != source_row["rng_seed"]
    assert copy_row["status"] == "draft"
    assert copy_row["actual_start"] is None
    assert copy_row["finished_at"] is None
    assert copy_row["event_date"] == source_row["event_date"]
    assert copy_row["venue"] == source_row["venue"]


def test_store_duplicate_ride_accepts_an_explicit_copy_name(
    tmp_path: Path,
) -> None:
    """Passing name= overrides the "(copy)" default."""
    db_path = tmp_path / "rides.db"
    source_id = _source_ride_with_timing_data(db_path, _solo_roster())
    store = Store.open(db_path)
    try:
        copy_id = store.duplicate_ride(source_id, name="Winter Loop")
    finally:
        store.close()

    reopened = Store.open(db_path)
    try:
        names = [row.name for row in reopened.rides()]
    finally:
        reopened.close()
    assert copy_id != source_id
    assert names == ["GORBA EPIC 2026", "Winter Loop"]


def test_store_duplicate_ride_keeps_the_source_untouched(tmp_path: Path) -> None:
    """Duplicating never mutates the source ride or its timing data."""
    db_path = tmp_path / "rides.db"
    roster = _pooled_roster()
    source_id = _source_ride_with_timing_data(db_path, roster)
    store = Store.open(db_path)
    try:
        store.duplicate_ride(source_id)
        source = store.load_engine(source_id)
    finally:
        store.close()

    assert source.state is RideStatus.RUNNING
    assert [crossing.entry_id for crossing in source.crossings] == [entry_key(roster, "12")]


def test_store_duplicate_ride_copies_the_hold_short_laps_policy(tmp_path: Path) -> None:
    """R-15 copies setup: the W4 policy column travels with the row."""
    db_path = tmp_path / "rides.db"
    source_id = _save_roster_ride(
        db_path,
        _solo_roster(),
        name="Hold Policy",
        hold_short_laps=True,
    )
    store = Store.open(db_path)
    try:
        copy_id = store.duplicate_ride(source_id)
    finally:
        store.close()

    copy_row = _fetch_ride_row(db_path, copy_id)
    assert copy_row["hold_short_laps"] == 1


def test_store_duplicate_ride_copies_first_and_last_name_columns(tmp_path: Path) -> None:
    """The copy's rider rows carry the split, not a single name."""
    db_path = tmp_path / "rides.db"
    source_id = _source_ride_with_timing_data(db_path, _pooled_roster())
    store = Store.open(db_path)
    try:
        copy_id = store.duplicate_ride(source_id)
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        copied = conn.execute(
            "SELECT first_name, last_name FROM rider"
            " WHERE entry_id IN (SELECT id FROM entry WHERE ride_id = ?) ORDER BY id",
            (copy_id,),
        ).fetchall()
    assert [tuple(row) for row in copied] == [("Alice", ""), ("A.", "Roy"), ("K.", "Singh")]


def test_store_duplicate_ride_gives_every_copied_entry_a_fresh_key(
    tmp_path: Path,
) -> None:
    """R-15: a copy's entries are new identities, never the source's.

    The catalog is the copy's own, and its entries carry their own
    stable keys -- the same fresh-``rng_seed`` rule one level down. A
    shared key would file the copy's crossings under the source's
    entry.
    """
    db_path = tmp_path / "rides.db"
    source = _pooled_roster()
    source_id = _source_ride_with_timing_data(db_path, source)
    store = Store.open(db_path)
    try:
        copy_id = store.duplicate_ride(source_id)
        copied = store.roster_for(copy_id)
    finally:
        store.close()

    copied_keys = [entry.key for entry in copied.entries]
    assert len(set(copied_keys)) == len(copied_keys)
    assert set(copied_keys).isdisjoint({entry.key for entry in source.entries})


def test_store_duplicate_ride_copies_the_entry_logo_card(tmp_path: Path) -> None:
    """R-15: an entry's logo_card copies over with the roster."""
    db_path = tmp_path / "rides.db"
    source_id = _source_ride_with_timing_data(db_path, _solo_roster())
    store = Store.open(db_path)
    try:
        with store._conn:
            store._conn.execute(
                "UPDATE entry SET logo_card = ? WHERE ride_id = ?",
                ("club-logo", source_id),
            )
        copy_id = store.duplicate_ride(source_id)
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        source_row = conn.execute(
            "SELECT logo_card FROM entry WHERE ride_id = ?", (source_id,)
        ).fetchone()
        copy_row = conn.execute(
            "SELECT logo_card FROM entry WHERE ride_id = ?", (copy_id,)
        ).fetchone()
    assert source_row is not None
    assert copy_row is not None
    assert copy_row["logo_card"] == source_row["logo_card"]
    assert copy_row["logo_card"] == "club-logo"


def test_store_duplicate_ride_unknown_ride_raises_naming_it(
    tmp_path: Path,
) -> None:
    """T-5: duplicating a ride id that never existed fails loudly."""
    Store.open(tmp_path / "rides.db").close()

    store = Store.open(tmp_path / "rides.db")
    try:
        with pytest.raises(RideNotFoundError, match=re.escape("no ride with id 999")):
            store.duplicate_ride(999)
    finally:
        store.close()


# ------------------------------------------- E7.3.1 audit_rows (R-38)


def test_store_audit_rows_projects_fields_newest_first(tmp_path: Path) -> None:
    """audit_rows returns the display shape, newest first by id."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={
                    "plate": "12",
                    "entry_id": "12",
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:02:00",
                },
            ),
        )
        store.append(
            ride_id,
            Event(
                action="edit_crossing",
                payload={
                    "entry_id": "12",
                    "seq": 1,
                    "previous_crossed_at": "2026-09-20T10:02:00",
                    "crossed_at": "2026-09-20T10:03:00",
                    "reason": "mis-keyed time",
                },
            ),
        )
        rows = store.audit_rows(ride_id)
    finally:
        store.close()

    assert rows == [
        AuditRow(
            when="10:03:00",
            action="edit_crossing",
            entry="12",
            reason="mis-keyed time",
        ),
        AuditRow(when="10:02:00", action="record_crossing", entry="12", reason=""),
        AuditRow(when="10:00:00", action="start", entry="", reason=""),
    ]


def test_store_audit_rows_for_a_ride_with_no_events_returns_empty(tmp_path: Path) -> None:
    """A known ride with no recorded events reads an empty trail."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())

        rows = store.audit_rows(ride_id)
    finally:
        store.close()

    assert rows == []


def test_store_audit_rows_entry_prefers_entry_id_then_plate_then_blank(
    tmp_path: Path,
) -> None:
    """Entry projects entry_id, falling back to plate, then blank."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        # A real record_crossing carries both; entry_id wins.
        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={
                    "plate": "77",
                    "entry_id": "12",
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:02:00",
                },
            ),
        )
        # The plate-only branch: the projection's `or` fallback (T-3).
        store.append(
            ride_id,
            Event(
                action="record_crossing",
                payload={"plate": "77", "lap": 2, "crossed_at": "2026-09-20T10:04:00"},
            ),
        )
        # shoe_reshuffle carries neither; entry stays blank.
        store.append(ride_id, Event(action="shoe_reshuffle", payload={"cycle": 2}))

        rows = store.audit_rows(ride_id)
    finally:
        store.close()

    assert [row.entry for row in rows] == ["", "77", "12"]


def test_store_audit_rows_when_renders_the_stored_at_as_local_time(
    tmp_path: Path,
) -> None:
    """When renders the stored at epoch as local HH:MM:SS."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )

        rows = store.audit_rows(ride_id)
    finally:
        store.close()

    assert rows[0].when == "10:00:00"


def test_store_audit_rows_scopes_to_the_requested_ride(tmp_path: Path) -> None:
    """One ride's trail never leaks another ride's rows."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        first = store.create_ride(_config(name="First"))
        second = store.create_ride(_config(name="Second"))
        store.append(first, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}))
        store.append(
            second, Event(action="start", payload={"actual_start": "2026-09-20T11:00:00"})
        )

        first_rows = store.audit_rows(first)
        second_rows = store.audit_rows(second)
    finally:
        store.close()

    assert [row.when for row in first_rows] == ["10:00:00"]
    assert [row.when for row in second_rows] == ["11:00:00"]


def test_store_audit_rows_unknown_ride_raises_naming_it(tmp_path: Path) -> None:
    """T-5: reading a ride id that never existed fails loudly."""
    Store.open(tmp_path / "rides.db").close()

    store = Store.open(tmp_path / "rides.db")
    try:
        with pytest.raises(RideNotFoundError, match=re.escape("no ride with id 999")):
            store.audit_rows(999)
    finally:
        store.close()


# ----------------- plan §8: roster plate changes in the audit table


def test_store_append_roster_event_inserts_an_audit_row_stamped_at_now(
    tmp_path: Path,
) -> None:
    """A roster event lands as one audit row stamped at append time."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        before = int(datetime.now(UTC).timestamp())
        store.append_roster_event(
            ride_id,
            "change_solo_plate",
            json.dumps({"display_name": "Alice", "old_plate": "12", "new_plate": "13"}),
        )
        after = int(datetime.now(UTC).timestamp())
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT at, action, payload_json FROM audit WHERE ride_id = ?", (ride_id,)
        ).fetchone()
    assert row is not None
    assert before <= row["at"] <= after
    assert row["action"] == "change_solo_plate"
    assert json.loads(row["payload_json"]) == {
        "display_name": "Alice",
        "old_plate": "12",
        "new_plate": "13",
    }


def test_store_append_roster_event_leaves_the_ride_status_and_updated_at_untouched(
    tmp_path: Path,
) -> None:
    """A plate change is audit-only: the ride row never moves."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        running = _fetch_ride_row(db_path, ride_id)
        store.append_roster_event(ride_id, "change_team_plate", json.dumps({"new_plate": "9"}))
        after = _fetch_ride_row(db_path, ride_id)
    finally:
        store.close()

    assert after["status"] == RideStatus.RUNNING
    assert after["status"] == running["status"]
    assert after["updated_at"] == running["updated_at"]


def test_store_append_roster_event_unknown_ride_raises_naming_it(tmp_path: Path) -> None:
    """T-5: a roster event for an unknown ride fails loudly."""
    Store.open(tmp_path / "rides.db").close()

    store = Store.open(tmp_path / "rides.db")
    try:
        with pytest.raises(RideNotFoundError, match=re.escape("no ride with id 999")):
            store.append_roster_event(999, "change_solo_plate", "{}")
    finally:
        store.close()


def test_store_audit_rows_entry_falls_back_through_plate_change_payload_keys(
    tmp_path: Path,
) -> None:
    """A plate-change row renders old_plate, new_plate, then name."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        store.append_roster_event(
            ride_id,
            "change_solo_plate",
            json.dumps({"display_name": "Alice", "old_plate": "12", "new_plate": "13"}),
        )
        store.append_roster_event(
            ride_id, "change_team_plate", json.dumps({"display_name": "A", "new_plate": "77"})
        )
        store.append_roster_event(
            ride_id, "change_pooled_rider_plate", json.dumps({"display_name": "Trail Blazers"})
        )

        rows = store.audit_rows(ride_id)
    finally:
        store.close()

    assert [row.entry for row in rows] == ["Trail Blazers", "77", "12"]


def test_store_audit_rows_given_a_dnf_row_renders_the_carried_display(
    tmp_path: Path,
) -> None:
    """A dnf row names the marked rider, never the bare entry id."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        store.append(
            ride_id,
            Event(
                action="dnf",
                payload={
                    "entry_id": "9",
                    "plate": "45",
                    "rider": True,
                    "reason": "mechanical failure",
                    "display": "45 · Sarah",
                },
            ),
        )

        rows = store.audit_rows(ride_id)
    finally:
        store.close()

    assert rows[0].entry == "45 · Sarah"


def test_store_audit_rows_given_a_dnf_row_without_a_display_falls_back(
    tmp_path: Path,
) -> None:
    """T-4 nullable: a stored row predating the display keeps its id."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        store.append(
            ride_id,
            Event(
                action="dnf",
                payload={
                    "entry_id": "9",
                    "plate": "45",
                    "rider": True,
                    "reason": "mechanical failure",
                },
            ),
        )

        rows = store.audit_rows(ride_id)
    finally:
        store.close()

    assert rows[0].entry == "9"


def test_store_audit_rows_given_a_tiebreak_draw_row_renders_the_payload_summary(
    tmp_path: Path,
) -> None:
    """R-14: a tiebreak_draw row reads its own summary, never blank.

    The draw's payload carries one row per entry plus the human
    ``summary`` naming them ("12 · 5H, 34 · AH"); the row list is not a
    single entry id, so the Entry cell reads the summary the engine
    wrote for exactly this projection.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        store.append(
            ride_id,
            Event(
                action="tiebreak_draw",
                payload={
                    "draws": [
                        {"entry_id": "12", "card": "5H"},
                        {"entry_id": "34", "card": "AH"},
                    ],
                    "summary": "12 · 5H, 34 · AH",
                },
            ),
        )

        rows = store.audit_rows(ride_id)
    finally:
        store.close()

    assert rows[0].entry == "12 · 5H, 34 · AH"


def test_store_audit_rows_given_a_tiebreak_draw_without_a_summary_stays_blank(
    tmp_path: Path,
) -> None:
    """T-4 nullable: a row with no summary keeps the empty-cell default.

    A hand-written or half-written draw row carries neither an entry id
    nor a summary, so the existing ``entry_id``/``plate`` chain still
    answers -- the summary is read *when present*, never assumed.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        store.append(ride_id, Event(action="tiebreak_draw", payload={"draws": []}))

        rows = store.audit_rows(ride_id)
    finally:
        store.close()

    assert rows[0].entry == ""


def test_store_load_engine_ignores_a_roster_plate_change_row(tmp_path: Path) -> None:
    """Replay skips a plate-change row: ``apply`` never sees it."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        store.append_roster_event(
            ride_id,
            "change_team_plate",
            json.dumps({"display_name": "A", "old_plate": "1", "new_plate": "2"}),
        )

        engine = store.load_engine(ride_id, roster=_replay_roster())
    finally:
        store.close()

    assert [event.action for event in engine.events] == ["start"]
    assert engine.state is RideStatus.RUNNING


# ------------------------------------------------- default_db_path
# E9.1.1: the bootstrap resolves the rides database path the same way
# settings.py's default_path resolves settings.json -- platformdirs,
# per-user, named "RiverCrossing" (the retired mockups'
# "PokerRunTracker" is superseded). The helper owns both the default
# and any explicit override so main() has exactly one place the path
# decision lives.


def test_default_db_path_returns_rides_db_under_the_user_data_dir() -> None:
    """The default db lives directly in platformdirs' user data dir."""
    path = store_module.default_db_path()

    assert path.name == "rides.db"
    assert path.parent == Path(user_data_dir("RiverCrossing", appauthor=False))


def test_default_db_path_calls_user_data_dir_with_appauthor_false(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    r"""The default db asks for ONE RiverCrossing folder, never two.

    platformdirs defaults ``appauthor`` to ``appname``, so the bare
    call lands in ``%LOCALAPPDATA%\RiverCrossing\RiverCrossing`` on
    Windows. ``appauthor=False`` asks for the single folder; macOS
    ignores ``appauthor`` entirely, so its path is unchanged.
    """
    calls: list[tuple[str, dict[str, object]]] = []

    def _record(appname: str, **kwargs: object) -> str:
        calls.append((appname, kwargs))
        return str(tmp_path)

    monkeypatch.setattr(store_module, "user_data_dir", _record)

    path = store_module.default_db_path()

    assert calls == [("RiverCrossing", {"appauthor": False})]
    assert path == tmp_path / "rides.db"


def test_default_db_path_given_an_override_returns_it_verbatim() -> None:
    """An explicit path wins untouched (tests, diagnostics)."""
    override = Path(tempfile.gettempdir()) / "rc-custom" / "rides.db"

    assert store_module.default_db_path(override) == override


def test_default_db_path_given_none_returns_the_default() -> None:
    """None means "no override": the platformdirs default stands."""
    assert store_module.default_db_path(None) == store_module.default_db_path()


# ------------------------------------------------ Phase 4 team logos
# (A team's logo_card round-trips through the entry table's Phase-1
# column instead of always storing NULL, and the rebuilt roster
# inherits the ride's rng_seed as its team_logo_seed so new teams
# auto-assign logo cards deterministically. Phase 3 retired the logo
# image column, so the card is the whole logo.)


def _team_of(roster: Roster) -> Entry:
    """Return *roster*'s single TEAM entry (its second entry)."""
    return roster.entries[1]


def test_store_save_roster_round_trips_a_teams_logo_card(tmp_path: Path) -> None:
    """Phase 4: logo_card is written and rebuilt, not NULLed."""
    db_path = tmp_path / "rides.db"
    roster = _pooled_roster()
    _team_of(roster).logo_card = "AS"
    ride_id = _save_roster_ride(
        db_path,
        roster,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    reloaded = _round_trip_roster(db_path, ride_id)

    assert _team_of(reloaded).logo_card == "AS"


# ------------------------------------------------ coverage-gap close
# R-71's >=90% line/branch audit (2026-09): the guard branches below
# were never exercised -- the exhausted-retry raise, save_roster's
# negative path, backup_now's two arms, and delete_ride's no-backing-
# path refusal (Store.__init__'s own docstring allows direct
# construction "tests only", so the no-path guard is reachable and
# must be pinned, not waived).


def test_store_open_transient_errors_exhausting_retries_raise_last_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An always-transient failure exhausts the budget, then raises."""
    calls = {"n": 0}

    def always_transient(_conn: sqlite3.Connection) -> None:
        calls["n"] += 1
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(store_module, "apply_pragmas", always_transient)

    with pytest.raises(sqlite3.OperationalError, match=re.escape("database is locked")):
        store_module.Store.open(tmp_path / "store.db")

    assert calls["n"] == store_module._OPEN_RETRY_ATTEMPTS


def test_store_save_roster_unknown_ride_raises_naming_it(tmp_path: Path) -> None:
    """T-5: save_roster's negative case names the missing ride."""
    Store.open(tmp_path / "rides.db").close()

    store = Store.open(tmp_path / "rides.db")
    try:
        with pytest.raises(RideNotFoundError, match=re.escape("no ride with id 999")):
            store.save_roster(999, _solo_roster())
    finally:
        store.close()


def test_store_backup_now_writes_a_manual_backup_of_the_open_database(
    tmp_path: Path,
) -> None:
    """R-54: backup_now writes a real backup the ride round-trips in."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Back me up"))
        backup_path = store.backup_now()
    finally:
        store.close()

    assert backup_path.is_file()
    assert backup_path in _backup_files(db_path)
    reopened = Store.open(backup_path)
    try:
        assert [ride.name for ride in reopened.rides()] == ["Back me up"]
        assert reopened.rides()[0].id == ride_id
    finally:
        reopened.close()


def test_store_backup_now_without_a_backing_path_refuses(tmp_path: Path) -> None:
    """A direct-constructed store (no path) cannot back up (T-5)."""
    conn = sqlite3.connect(str(tmp_path / "direct.db"))
    conn.row_factory = sqlite3.Row
    try:
        store = Store(conn)

        with pytest.raises(StoreError, match=re.escape("store has no backing path")):
            store.backup_now()
    finally:
        conn.close()


def test_store_delete_ride_without_a_backing_path_refuses_before_deleting(
    tmp_path: Path,
) -> None:
    """A direct-constructed store refuses to delete: no backup path."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="No path"))
    finally:
        store.close()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        direct = Store(conn)

        with pytest.raises(StoreError, match=re.escape("store has no backing path")):
            direct.delete_ride(ride_id, "No path")
    finally:
        conn.close()

    reopened = Store.open(db_path)
    try:
        assert [ride.name for ride in reopened.rides()] == ["No path"]
    finally:
        reopened.close()


def test_store_load_roster_gives_the_rebuilt_roster_the_rides_logo_seed(
    tmp_path: Path,
) -> None:
    """Phase 4: a reloaded roster auto-assigns from the ride's rng_seed.

    ``_load_roster`` reads the ride row's ``rng_seed`` and passes it
    as ``team_logo_seed=``, so a team added after reload draws the
    same deterministic first code on every reload of the same ride.
    """
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _pooled_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    def _new_team_logo() -> str:
        roster = _round_trip_roster(db_path, ride_id)
        created = roster.create_team_entry(
            display_name="Late Team",
            riders=[
                Rider(first_name="A", last_name="B", plate="90"),
                Rider(first_name="C", last_name="D", plate="91"),
            ],
        )
        assert created.logo_card is not None
        return created.logo_card

    assert _new_team_logo() == _new_team_logo()


# ============================================================ W8
# Zero-rider TEAM entries: save_roster writes the entry row with no
# rider rows, and _load_roster rebuilds the empty team as-is (W8).


def test_store_save_roster_zero_rider_team_round_trips_with_no_rider_rows(tmp_path: Path) -> None:
    """An empty pooled team persists as one entry with no riders."""
    db_path = tmp_path / "rides.db"
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_empty_team(display_name="Trail Blazers", logo_card="AS")
    ride_id = _save_roster_ride(
        db_path,
        roster,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    reloaded = _round_trip_roster(db_path, ride_id)

    (entry,) = reloaded.entries
    assert (entry.display_name, entry.type.value, entry.team_size) == (
        "Trail Blazers",
        "team",
        0,
    )
    assert entry.riders == []
    assert (entry.logo_card, entry.plate) == ("AS", "1")


def test_store_save_roster_zero_rider_relay_team_keeps_its_relay_plate(tmp_path: Path) -> None:
    """W8: an empty relay team round-trips its own plate, no riders."""
    db_path = tmp_path / "rides.db"
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_empty_team(display_name="Trail Blazers", plate="88")
    ride_id = _save_roster_ride(
        db_path,
        roster,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.TEAM_RELAY,
    )

    reloaded = _round_trip_roster(db_path, ride_id)

    (entry,) = reloaded.entries
    assert entry.plate == "88"
    assert entry.riders == []


# ============================================================ C1
# The ride-logo re-materialization (C1).


def test_store_load_engine_rematerializes_the_stored_ride_logo(tmp_path: Path) -> None:
    """C1: a reloaded ride's logo BLOB becomes a renderable file path.

    ``ride.logo_png`` is stored as a BLOB (spec §2) but every logo
    surface renders from a file, so loading a ride writes the bytes
    back out and hands the header a path.
    """
    db_path = tmp_path / "rides.db"
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(base64.b64decode(_TINY_PNG_B64))
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(logo_path=logo_path))

        engine = store.load_engine(ride_id, _replay_roster())
    finally:
        store.close()

    materialized = engine.config.logo_path
    assert materialized is not None
    assert materialized.read_bytes() == base64.b64decode(_TINY_PNG_B64)


@pytest.mark.parametrize("logo_bytes", [None, b""], ids=["null", "empty"])
def test_store_load_engine_given_no_logo_bytes_leaves_logo_path_none(
    tmp_path: Path, logo_bytes: bytes | None
) -> None:
    """T-4 nullable: NULL and empty BLOBs both mean "no logo"."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config())
        with store._conn:
            store._conn.execute("UPDATE ride SET logo_png = ? WHERE id = ?", (logo_bytes, ride_id))

        engine = store.load_engine(ride_id, _replay_roster())
    finally:
        store.close()

    assert engine.config.logo_path is None


def test_store_load_engine_materializes_each_ride_logo_to_its_own_temp_file(
    tmp_path: Path,
) -> None:
    """CWE-377: each load writes a fresh, uniquely-named temp file.

    ``tempfile.mkstemp`` creates the file atomically under a random
    name (``O_CREAT``/``O_EXCL``), so there is no data-derived path for
    a pre-planted symlink to sit at -- and two loads of one ride can
    never share, or overwrite, a single file.
    """
    db_path = tmp_path / "rides.db"
    logo_bytes = base64.b64decode(_TINY_PNG_B64)
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(logo_bytes)
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(logo_path=logo_path))
        first = store.load_engine(ride_id, _replay_roster()).config.logo_path
        second = store.load_engine(ride_id, _replay_roster()).config.logo_path
    finally:
        store.close()

    assert isinstance(first, Path)
    assert isinstance(second, Path)
    assert first != second
    assert first.read_bytes() == logo_bytes
    assert second.read_bytes() == logo_bytes


# --------------------------------------- D2: updating a stored ride


def test_store_update_ride_config_rewrites_the_editable_columns(tmp_path: Path) -> None:
    """D2: an edited ride's own settings land back on the ride row."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Before"))
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )

        store.update_ride_config(
            ride_id,
            _config(
                name="After",
                venue="New Venue",
                lap_km=6.5,
                organizer="Org 2",
                scorer="Scorer 2",
                planned_duration_s=7200,
                min_lap_s=90,
                max_team_size=6,
                deck_count=2,
                jokers_per_deck=0,
                max_cards=5,
                tiebreak_order=("high_card", "laps", "total_time"),
            ),
        )
        engine = store.load_engine(ride_id, _replay_roster())
    finally:
        store.close()

    config = engine.config
    assert (
        config.name,
        config.venue,
        config.lap_km,
        config.organizer,
        config.scorer,
        config.planned_duration_s,
        config.min_lap_s,
        config.max_team_size,
        config.deck_count,
        config.jokers_per_deck,
        config.max_cards,
        config.tiebreak_order,
    ) == (
        "After",
        "New Venue",
        6.5,
        "Org 2",
        "Scorer 2",
        7200,
        90,
        6,
        2,
        0,
        5,
        ("high_card", "laps", "total_time"),
    )
    # The audit log is the live ride, not a setting: an edit never
    # rewrites it, so the ride stays RUNNING after its setup changes.
    assert engine.state is RideStatus.RUNNING


def test_store_update_ride_config_keeps_the_stored_logo_when_none_is_given(
    tmp_path: Path,
) -> None:
    """D2: an edit that picks no logo does not wipe the stored one."""
    db_path = tmp_path / "rides.db"
    logo_path = tmp_path / "logo.png"
    logo_path.write_bytes(base64.b64decode(_TINY_PNG_B64))
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(logo_path=logo_path))

        store.update_ride_config(ride_id, _config(name="Renamed"))
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        stored = conn.execute("SELECT logo_png FROM ride WHERE id = ?", (ride_id,)).fetchone()[0]

    assert stored == base64.b64decode(_TINY_PNG_B64)


def test_store_update_ride_config_given_a_logo_path_rewrites_the_blob(
    tmp_path: Path,
) -> None:
    """D2: a logo picked in Edit Ride replaces the stored BLOB."""
    db_path = tmp_path / "rides.db"
    logo_path = tmp_path / "new-logo.png"
    logo_path.write_bytes(base64.b64decode(_TINY_PNG_B64))
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Before"))

        store.update_ride_config(ride_id, _config(name="After", logo_path=logo_path))
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        stored = conn.execute("SELECT logo_png FROM ride WHERE id = ?", (ride_id,)).fetchone()[0]

    assert stored == base64.b64decode(_TINY_PNG_B64)


def test_store_update_ride_config_unknown_ride_raises_naming_it(tmp_path: Path) -> None:
    """T-5: editing a ride id that never existed fails loudly."""
    db_path = tmp_path / "rides.db"
    Store.open(db_path).close()

    store = Store.open(db_path)
    try:
        with pytest.raises(RideNotFoundError, match=re.escape("no ride with id 999")):
            store.update_ride_config(999, _config())
    finally:
        store.close()


# ============================================================ retired
# The pooled-live-move replay seam: a dissolved entry that carries
# recorded data is persisted with ``entry.retired = 1`` so its stable
# key comes back on reload -- the engine's replay resolves every stored
# ``record_crossing`` row by that key. ``retired`` also relaxes the
# per-ride plate uniqueness to live rows (the partial unique index),
# because a solo->team move makes the destination team's derived plate
# equal the retired solo entry's.


def _retired_roster() -> Roster:
    """Build the roster a solo->team move leaves behind (arrange).

    Solo "1" carries recorded data, so the move that empties it retires
    it (never discards it); Team B adopts the lowest-numbered member's
    plate, now "1" -- the live/retired plate collision the partial
    unique index exists for.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    solo = roster.create_solo_entry(first_name="Alice", last_name="", plate="1")
    team = roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A.", last_name="Roy", plate="3"),
            Rider(first_name="K.", last_name="Singh", plate="4"),
        ],
    )
    roster.mark_has_data(solo)
    roster.move_rider(solo.riders[0], to_entry=team)
    return roster


def test_store_save_roster_round_trips_retired_entries_with_their_keys(
    tmp_path: Path,
) -> None:
    """A retired entry's stable key survives save_roster -> roster_for.

    The key is the replay seam (E3.1.2): without it the reloaded roster
    cannot resolve the pre-move ``record_crossing`` rows and the ride
    will not reopen.
    """
    db_path = tmp_path / "rides.db"
    roster = _retired_roster()
    (retired,) = roster.retired_entries
    ride_id = _save_roster_ride(
        db_path,
        roster,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    rebuilt = _round_trip_roster(db_path, ride_id)

    assert [entry.key for entry in rebuilt.retired_entries] == [retired.key]
    assert rebuilt.entry_by_key(retired.key) is rebuilt.retired_entries[0]


def test_store_save_roster_keeps_a_retired_entry_out_of_the_live_entries(
    tmp_path: Path,
) -> None:
    """Only the live entry reconstructs into ``entries``."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _retired_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    rebuilt = _round_trip_roster(db_path, ride_id)

    assert [(entry.display_name, entry.team_size) for entry in rebuilt.entries] == [
        ("Trail Blazers", 3)
    ]


def test_store_save_roster_writes_the_retired_flag_on_each_entry_row(
    tmp_path: Path,
) -> None:
    """The entry rows carry 0 live, 1 retired -- one per entry."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _retired_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    with closing(sqlite3.connect(str(db_path))) as conn:
        rows = conn.execute(
            "SELECT plate, retired FROM entry WHERE ride_id = ? ORDER BY id", (ride_id,)
        ).fetchall()

    assert rows == [("1", 0), ("1", 1)]


def test_store_save_roster_retains_a_retired_plate_a_live_entry_adopted(
    tmp_path: Path,
) -> None:
    """The partial index lets a retired plate equal a live one (S1).

    A solo->team move re-derives the destination team's plate to the
    lowest-numbered member's -- exactly the plate the dissolved solo
    entry still holds.
    """
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _retired_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    rebuilt = _round_trip_roster(db_path, ride_id)

    assert [entry.plate for entry in (*rebuilt.entries, *rebuilt.retired_entries)] == ["1", "1"]


def test_store_load_roster_gives_a_retired_entry_no_riders(tmp_path: Path) -> None:
    """A retired entry was emptied by the move that retired it."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _retired_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    rebuilt = _round_trip_roster(db_path, ride_id)

    (retired,) = rebuilt.retired_entries
    assert (retired.type.value, retired.riders) == ("solo", [])


def test_store_save_roster_replaces_the_previous_retired_entries(tmp_path: Path) -> None:
    """A second save is a snapshot: an earlier retired row is gone."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _retired_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    store = Store.open(db_path)
    try:
        store.save_roster(ride_id, _pooled_roster())
    finally:
        store.close()

    rebuilt = _round_trip_roster(db_path, ride_id)

    assert (
        len(rebuilt.entries),
        rebuilt.retired_entries,
    ) == (2, ())


def test_store_entry_plate_uniqueness_still_applies_to_live_entries(
    tmp_path: Path,
) -> None:
    """Two live entries may not share a plate (the partial index)."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _solo_roster(),
        entry_mode=EntryMode.SOLO,
        plate_model=PlateModel.RIDER_POOLED,
    )
    store = Store.open(db_path)
    try:
        with pytest.raises(
            sqlite3.IntegrityError,
            match=re.escape("UNIQUE constraint failed: entry.ride_id, entry.plate"),
        ):
            store._conn.execute(
                "INSERT INTO entry"
                " (ride_id, plate, key, display_name, type, team_size, status, retired)"
                " VALUES (?, '12', 'duplicate-key', 'Copy', 'solo', 1, 'active', 0)",
                (ride_id,),
            )
    finally:
        store.close()


def test_store_rides_counts_live_entries_not_retired_ones(tmp_path: Path) -> None:
    """The library's Entries column counts the live roster alone."""
    db_path = tmp_path / "rides.db"
    ride_id = _save_roster_ride(
        db_path,
        _retired_roster(),
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )

    store = Store.open(db_path)
    try:
        counts = {row.id: row.entries for row in store.rides()}
    finally:
        store.close()

    assert counts[ride_id] == 1


def test_store_duplicate_ride_copies_retired_entries_with_fresh_keys(
    tmp_path: Path,
) -> None:
    """R-15: the copy keeps the key history, under fresh keys."""
    db_path = tmp_path / "rides.db"
    source = _retired_roster()
    (source_retired,) = source.retired_entries
    source_id = _save_roster_ride(
        db_path,
        source,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    store = Store.open(db_path)
    try:
        copy_id = store.duplicate_ride(source_id)
        copied = store.roster_for(copy_id)
    finally:
        store.close()

    (copied_retired,) = copied.retired_entries
    assert (copied_retired.plate, copied_retired.display_name) == ("1", "Alice")
    assert {copied_retired.key, *(entry.key for entry in copied.entries)}.isdisjoint(
        {source_retired.key, *(entry.key for entry in source.entries)}
    )
