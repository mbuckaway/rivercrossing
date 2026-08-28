# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.store (E5.1.1: schema + migrations).

Tests first (R-70), against a ``store`` package that did not exist
yet. E5.1.1's surface is deliberately small: ``Store.open`` creates
and migrates a fresh database (WAL + NORMAL + foreign_keys ON per
spec §2), re-opening is idempotent, ``create_ride`` persists a
:class:`RideConfig` row (logo BLOB, JSON tiebreak order, DB-owned
seed), ``rides()`` lists the library, and a database written by a
newer build refuses to open with the version named.

No mocks anywhere: every test drives real sqlite3 against a
``tmp_path`` file (the task's own "no mocks of sqlite3 beyond
tmp_path DB files" rule). Assertions that inspect stored columns read
the file back through a second, independent connection -- the point is
what landed on disk, not what the facade keeps in memory.
"""

import base64
import json
import re
import sqlite3
from contextlib import closing
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING

import pytest

from rivercrossing.ride import RideConfig, RideStatus
from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.store import FutureSchemaVersionError, RideRow, Store, StoreError
from rivercrossing.store.migrations import LATEST_SCHEMA_VERSION

if TYPE_CHECKING:
    from pathlib import Path

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


# ------------------------------------------------------------- open


def test_store_open_fresh_db_creates_all_spec_tables(tmp_path: Path) -> None:
    """A fresh database opens with all spec tables plus the ledger."""
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
        assert expected <= names
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_store_open_records_ledger_at_latest_version(tmp_path: Path) -> None:
    """schema_version holds exactly one row at LATEST_SCHEMA_VERSION."""
    db_path = tmp_path / "rides.db"

    Store.open(db_path).close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        assert (
            conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
            == LATEST_SCHEMA_VERSION
        )
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1


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
                " (ride_id, plate, display_name, type, team_size, status)"
                " VALUES (999, 'P1', 'ghost', 'solo', 1, 'active')"
            )
    finally:
        store.close()
    # WAL is a persistent file property, so a later connection sees it.
    with closing(sqlite3.connect(str(db_path))) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_store_open_idempotent_reopen_runs_no_duplicate_migrations(
    tmp_path: Path,
) -> None:
    """Reopening a migrated database is a no-op that keeps its rides."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    ride_id = store.create_ride(_config(name="Idempotent"))
    store.close()

    reopened = Store.open(db_path)
    try:
        assert reopened.rides() == [
            RideRow(
                id=ride_id,
                name="Idempotent",
                event_date=date(2026, 9, 20),
                status=RideStatus.DRAFT,
            )
        ]
    finally:
        reopened.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        assert conn.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 1
        assert (
            conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0]
            == LATEST_SCHEMA_VERSION
        )


def test_store_open_migrates_v0_empty_database_to_v1(tmp_path: Path) -> None:
    """A v0 database (no schema, no ledger) upgrades to v1 on open."""
    db_path = tmp_path / "v0.db"
    conn = sqlite3.connect(str(db_path))
    conn.close()

    store = Store.open(db_path)
    try:
        assert store.rides() == []
    finally:
        store.close()

    with closing(sqlite3.connect(str(db_path))) as conn:
        names = {
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
        assert {"ride", "entry", "rider", "crossing", "card", "app_session", "audit"} <= names
        assert conn.execute("SELECT version FROM schema_version WHERE id = 1").fetchone()[0] == 1


def test_store_open_future_schema_version_refuses_with_version_named(
    tmp_path: Path,
) -> None:
    """A newer-build database refuses to open, naming the version."""
    db_path = tmp_path / "future.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "CREATE TABLE schema_version (id INTEGER PRIMARY KEY, version INTEGER NOT NULL)"
        )
        conn.execute("INSERT INTO schema_version (id, version) VALUES (1, 99)")
        conn.commit()
    finally:
        conn.close()

    with pytest.raises(FutureSchemaVersionError, match=re.escape("99")):
        Store.open(db_path)


def test_store_open_migration_failure_rolls_back_all_ddl(tmp_path: Path) -> None:
    """A mid-migration failure rolls back earlier DDL."""
    db_path = tmp_path / "conflict.db"
    conn = sqlite3.connect(str(db_path))
    try:
        # A v0-era table colliding with the last migration statement.
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
        assert "ride" not in names  # earlier DDL was rolled back, not half-created
        assert "audit" in names  # the v0 conflicting table survived
        assert "schema_version" in names  # ledger bootstrapped, no version row
        assert check.execute("SELECT COUNT(*) FROM schema_version").fetchone()[0] == 0


def test_store_future_schema_version_error_is_a_store_error() -> None:
    """The refusal surfaces as a StoreError subclass."""
    assert issubclass(FutureSchemaVersionError, StoreError)


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
