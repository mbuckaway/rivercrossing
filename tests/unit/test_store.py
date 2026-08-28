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

from rivercrossing.ride import Event, RideConfig, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import (
    FutureSchemaVersionError,
    RideNotFoundError,
    RideRow,
    SessionState,
    Store,
    StoreError,
)
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


# ----------------------------------------- E5.1.2 append + load_engine


def _replay_roster() -> Roster:
    """Build the MIXED rider_pooled roster load_engine tests pass in."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(name="Alice", plate="12")
    return roster


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
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(min_lap_s=1))
        start_event = Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        crossing_event = Event(
            action="record_crossing",
            payload={
                "plate": "12",
                "entry_id": "12",
                "lap": 1,
                "crossed_at": "2026-09-20T10:02:00",
            },
        )
        store.append(ride_id, start_event)
        store.append(ride_id, crossing_event)

        engine = store.load_engine(ride_id, _replay_roster())
    finally:
        store.close()

    assert engine.state is RideStatus.RUNNING
    assert len(engine.crossings) == 1
    assert engine.crossings[0].entry_id == "12"
    assert engine.crossings[0].crossed_at == datetime(2026, 9, 20, 10, 2)  # noqa: DTZ001
    assert engine.events == (start_event, crossing_event)


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
    assert engine.events == (start_event,)
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
                action="set_start_time",
                payload={
                    "actual_start": "2026-09-20T09:55:00",
                    "previous_start": "2026-09-20T10:00:00",
                },
            ),
        )

        engine = store.load_engine(ride_id, _replay_roster())
    finally:
        store.close()

    assert [e.action for e in engine.events] == ["start", "record_crossing", "set_start_time"]
    assert engine.lap_times("12") == (420.0,)


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
        store.append(
            ride_id,
            Event(
                action="deal_manual",
                payload={"plate": "12", "entry_id": "12", "card": "AS", "reason": "manual"},
            ),
        )

        engine = store.load_engine(ride_id, _replay_roster())
    finally:
        store.close()

    assert engine._shoe.dealt == 1  # one card off the fresh replay shoe
    assert engine.config.deck_count == 8
    assert engine._shoe.remaining == 8 * 54 - 1


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
