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
from pathlib import Path

import pytest
from platformdirs import user_data_dir

import rivercrossing.store as store_module
from rivercrossing.ride import Event, RideConfig, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.store import (
    FutureSchemaVersionError,
    RideNameMismatchError,
    RideNotFoundError,
    RideRow,
    RideRunningError,
    SessionState,
    Store,
    StoreError,
    backup,
)
from rivercrossing.store.migrations import LATEST_SCHEMA_VERSION
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
    before (test_store_open_migration_failure_rolls_back_all_ddl).
    """
    calls = {"n": 0}

    def failing_apply(_conn: sqlite3.Connection) -> None:
        calls["n"] += 1
        raise sqlite3.OperationalError("table ride already exists")

    monkeypatch.setattr(store_module, "apply_pragmas", failing_apply)

    with pytest.raises(sqlite3.OperationalError, match=re.escape("already exists")):
        store_module.Store.open(tmp_path / "store.db")

    assert calls["n"] == 1


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
    assert previous.ended_at == datetime.fromtimestamp(  # noqa: DTZ006 -- naive local, _from_epoch's inverse
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
    assert previous.ended_at == datetime.fromtimestamp(  # noqa: DTZ006 -- naive local, _from_epoch's inverse
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
        datetime(2026, 9, 20, 12, 37).timestamp()  # noqa: DTZ001 -- local epoch, what the store writes
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
    assert previous.ended_at == datetime.fromtimestamp(  # noqa: DTZ006 -- naive local, _from_epoch's inverse
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
    itself, in FK-safe order, in one transaction.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="Dependents"))
        store.append(
            ride_id,
            Event(
                action="start",
                payload={"actual_start": "2026-09-20T10:00:00"},
            ),
        )
        with store._conn:
            entry_id = store._conn.execute(
                "INSERT INTO entry (ride_id, plate, display_name, type, team_size, status)"
                " VALUES (?, '12', 'Alice', 'solo', 1, 'active')",
                (ride_id,),
            ).lastrowid
            rider_id = store._conn.execute(
                "INSERT INTO rider (entry_id, name, plate, sort_order)"
                " VALUES (?, 'Alice', '12', 1)",
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
    roster.create_solo_entry(name="Alice", plate="12")
    return roster


def _pooled_roster() -> Roster:
    """Build the MIXED rider_pooled roster E5.4.1 round-trips.

    One solo entry plus one team of two riders, each carrying their
    own plate -- the team's derived plate is the lowest ("77").
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(name="Alice", plate="12")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[Rider(name="A. Roy", plate="77"), Rider(name="K. Singh", plate="78")],
    )
    return roster


def _relay_roster() -> Roster:
    """Build the MIXED team_relay roster E5.4.1 round-trips.

    The team carries one plate ("88"); its riders are plateless
    (S1 -- the plate belongs to the entry, not the individual).
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.create_solo_entry(name="Alice", plate="12")
    roster.create_team_entry(
        display_name="Moss Ridge",
        riders=[Rider(name="R. Dubois"), Rider(name="M. Chen")],
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
    assert (rider.name, rider.plate, rider.sort_order) == ("Alice", "12", 0)


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
    assert [(rider.name, rider.plate) for rider in team.riders] == [
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
    assert [rider.name for rider in team.riders] == ["R. Dubois", "M. Chen"]


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
    start + crossing and resolves the recorded plate "12" through the
    reconstructed roster -- with an empty roster the crossing would be
    refused (unknown_plate) and never recorded, so this genuinely
    proves the roster came back from the entry/rider tables.
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
                    "entry_id": "12",
                    "lap": 1,
                    "crossed_at": "2026-09-20T10:02:00",
                },
            ),
        )

        engine = store.load_engine(ride_id)
    finally:
        store.close()

    assert engine.state is RideStatus.RUNNING
    assert [crossing.entry_id for crossing in engine.crossings] == ["12"]
    assert engine.lap_times("12") == (120.0,)


# ------------------------------ E5.4.1 duplicate_ride (R-15)


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
                    "entry_id": "12",
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
    """R-15: the copy is a new DRAFT ride with the roster, no timing."""
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
    assert [row.status for row in rows] == [RideStatus.DRAFT, RideStatus.DRAFT]
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
    source_id = _source_ride_with_timing_data(db_path, _pooled_roster())
    store = Store.open(db_path)
    try:
        store.duplicate_ride(source_id)
        source = store.load_engine(source_id)
    finally:
        store.close()

    assert source.state is RideStatus.RUNNING
    assert [crossing.entry_id for crossing in source.crossings] == ["12"]


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
            who="scorer",
            action="edit_crossing",
            entry="12",
            reason="mis-keyed time",
        ),
        AuditRow(when="10:02:00", who="scorer", action="record_crossing", entry="12", reason=""),
        AuditRow(when="10:00:00", who="scorer", action="start", entry="", reason=""),
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
    assert path.parent == Path(user_data_dir("RiverCrossing"))


def test_default_db_path_given_an_override_returns_it_verbatim() -> None:
    """An explicit path wins untouched (tests, diagnostics)."""
    override = Path("/tmp/rc-custom/rides.db")  # noqa: S108 -- a stored value, never opened here

    assert store_module.default_db_path(override) == override


def test_default_db_path_given_none_returns_the_default() -> None:
    """None means "no override": the platformdirs default stands."""
    assert store_module.default_db_path(None) == store_module.default_db_path()
