# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for rivercrossing.store.backup (E5.3.1, R-54).

Tests first, against a ``backup`` module that did not exist yet. The
contract, transcribed from module-skeletons.md S4 and task-brief
E5.3.1:

* ``backup.run(db_path, keep=20)`` copies the SQLite file to a
  timestamped backup in a sibling ``<db>.backups/`` directory and
  prunes to the newest ``keep`` (rotation at keep+1 the oldest goes);
  it returns the new backup's path. A WAL database (the R-50 store's
  own journal mode) is checkpointed through a short-lived second
  connection before the copy, so the main file alone is a complete,
  consistent snapshot -- the ``-wal``/``-shm`` sidecars are never
  copied.
* ``backup.schedule_hourly(runner, *, clock)`` is the minimal
  scheduler seam E5.4 wires a real timer to: each ``tick()`` checks
  the injected clock and runs *runner* exactly once per hour boundary
  crossed. The first tick only seeds the last-seen hour (R-54's "on
  open" backup is a separate ``run`` call, not the hourly tick).
* ``backup.restore(src, dst)`` copies a backup file back over the
  live path and clears stale WAL sidecars (a fresh main file must
  never pair with a ``-wal`` written against the old one).

No mocks anywhere: every test drives real sqlite3 and real files
under ``tmp_path`` (the same "no mocks of sqlite3 beyond tmp_path DB
files" rule test_store.py follows). The only injected pieces are
clocks -- the same injectable-clock convention E4/E5.3 use for
deterministic timestamps -- and the rotation property below fuzzes
the one real invariant: after *runs* backups with *keep* retained,
exactly ``min(runs, keep)`` files remain and they are the newest.
"""

import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path  # noqa: TC003 -- pytest evaluates test-param annotations at collection
from typing import TYPE_CHECKING

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rivercrossing.ride import Event, RideConfig
from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.store import Store, backup
from rivercrossing.store.migrations import StoreError

if TYPE_CHECKING:
    from collections.abc import Callable

# The same always-valid kwarg set test_store.py builds from (T-8's
# one-focused-assertion spirit, applied to arrange too).
_VALID_KWARGS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "event_date": date(2026, 9, 20),
    "venue": "Sea to Sky Gondola",
    "lap_km": 8.0,
    "organizer": "GORBA",
    "scorer": "K. Singh",
    "planned_start": datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, RideConfig's own contract
    "planned_duration_s": 21600,
    "min_lap_s": 1080,
    "entry_mode": EntryMode.MIXED,
    "plate_model": PlateModel.RIDER_POOLED,
}


def _config(**overrides: object) -> RideConfig:
    """Build a valid RideConfig, overriding only what a test names."""
    return RideConfig(**{**_VALID_KWARGS, **overrides})  # type: ignore[arg-type]


@dataclass
class _FakeClock:
    """A mutable clock whose ``now()`` the caller advances by hand."""

    current: datetime
    step: timedelta = field(default=timedelta(minutes=1))

    def __call__(self) -> datetime:
        return self.current

    def advance(self) -> None:
        self.current += self.step


def _create_store(path: Path, name: str = "GORBA EPIC 2026") -> int:
    """Create one ride in a fresh store; return its id."""
    store = Store.open(path)
    try:
        return store.create_ride(_config(name=name))
    finally:
        store.close()


def _backup_files(db_path: Path) -> list[Path]:
    """Return *db_path*'s backup files, newest first by name."""
    directory = backup.backup_dir_for(db_path)
    if not directory.is_dir():
        return []
    return sorted(directory.glob("*.db"), reverse=True)


def _write_backups(count: int, runner: Callable[[], Path]) -> list[Path]:
    """Run *count* backups via *runner*; return the written paths.

    Keeps the rotation tests single-Act (T-8): the only Act is "prune
    to keep"; the repeated ``run`` calls are setup, and the caller's
    *runner* closure owns the per-run clock advance.
    """
    return [runner() for _ in range(count)]


# ------------------------------------------------------------- run


def test_backup_run_copies_db_to_timestamped_sibling_file(tmp_path: Path) -> None:
    """Run writes one timestamped ``.db`` beside the database."""
    db_path = tmp_path / "rides.db"
    _create_store(db_path)

    result = backup.run(db_path)

    assert result.is_file()
    assert result.parent == db_path.parent / "rides.db.backups"
    assert result.name.startswith("rides.")
    assert result.name.endswith(".db")
    # The timestamp is the sortable UTC-rendered instant, e.g.
    # 20260920-100000-123456.
    assert len(result.stem) == len("rides.20260920-100000-123456")


def test_backup_run_backup_reopens_with_integrity_ok(tmp_path: Path) -> None:
    """The written backup reopens with a clean integrity check."""
    db_path = tmp_path / "rides.db"
    _create_store(db_path)

    result = backup.run(db_path)

    reopened = Store.open(result)
    try:
        assert len(reopened.rides()) == 1
        assert reopened.rides()[0].name == "GORBA EPIC 2026"
    finally:
        reopened.close()
    with closing(sqlite3.connect(str(result))) as conn:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_backup_run_live_wal_store_checkpoints_before_copy(tmp_path: Path) -> None:
    """A live store's committed data survives into the backup (WAL).

    The R-50 store runs journal_mode=WAL, so committed-but-not-yet-
    checkpointed pages live in the ``-wal`` sidecar. run() folds the
    WAL into the main file through a second connection before copying
    -- otherwise the copy would miss the ride. The store stays OPEN
    for the whole test to prove the live path.
    """
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(name="WAL Live"))
        store.append(
            ride_id,
            Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"}),
        )
        result = backup.run(db_path)
    finally:
        store.close()

    reopened = Store.open(result)
    try:
        assert [ride.name for ride in reopened.rides()] == ["WAL Live"]
    finally:
        reopened.close()


def test_backup_run_copies_only_the_main_file_not_wal_sidecars(
    tmp_path: Path,
) -> None:
    """The backup directory holds one .db, never -wal/-shm files."""
    db_path = tmp_path / "rides.db"
    _create_store(db_path)

    backup.run(db_path)

    names = [path.name for path in backup.backup_dir_for(db_path).iterdir()]
    assert all(name.endswith(".db") for name in names)
    assert not any("-wal" in name or "-shm" in name for name in names)


def test_backup_run_prunes_to_keep_rotating_at_keep_plus_one(tmp_path: Path) -> None:
    """At keep+1 backups the oldest is pruned (rotation, R-54)."""
    db_path = tmp_path / "rides.db"
    _create_store(db_path)
    clock = _FakeClock(datetime(2026, 9, 20, 10, 0))  # noqa: DTZ001 -- fake wall clock

    def _advance_and_run() -> Path:
        clock.advance()
        return backup.run(db_path, keep=20, clock=clock)

    _write_backups(21, _advance_and_run)

    files = _backup_files(db_path)
    assert len(files) == 20
    # The newest 20 survive: the oldest (10:01) was pruned at 10:21.
    assert files[-1].name.endswith("20260920-100200-000000.db")
    assert files[0].name.endswith("20260920-102100-000000.db")


@pytest.mark.parametrize("keep", [19, 20])
def test_backup_run_keeps_everything_below_the_keep_cap(tmp_path: Path, keep: int) -> None:
    """Below the cap nothing is pruned -- keep is a ceiling (T-4).

    Rows cover keep's max-1 (19) and max (20): five backups against
    each retain all five. The min boundary (keep=1) has its own test,
    since at keep=1 five runs legitimately prune to one.
    """
    db_path = tmp_path / "rides.db"
    _create_store(db_path)
    clock = _FakeClock(datetime(2026, 9, 20, 10, 0))  # noqa: DTZ001 -- fake wall clock

    def _advance_and_run() -> Path:
        clock.advance()
        return backup.run(db_path, keep=keep, clock=clock)

    _write_backups(5, _advance_and_run)

    assert len(_backup_files(db_path)) == 5


def test_backup_run_boundary_keep_one_keeps_only_the_newest(
    tmp_path: Path,
) -> None:
    """T-4 boundary: keep=1 retains exactly the newest backup."""
    db_path = tmp_path / "rides.db"
    _create_store(db_path)
    clock = _FakeClock(datetime(2026, 9, 20, 10, 0))  # noqa: DTZ001 -- fake wall clock

    first = backup.run(db_path, keep=1, clock=clock)
    clock.advance()
    second = backup.run(db_path, keep=1, clock=clock)

    files = _backup_files(db_path)
    assert files == [second]
    assert first not in files


def test_backup_run_keep_zero_raises_naming_the_bound(tmp_path: Path) -> None:
    """T-5: keep < 1 is refused loudly."""
    db_path = tmp_path / "rides.db"
    _create_store(db_path)

    with pytest.raises(ValueError, match=re.escape("keep must be at least 1")):
        backup.run(db_path, keep=0)


def test_backup_run_missing_source_db_raises_naming_it(tmp_path: Path) -> None:
    """T-5: backing up a path that is not a file fails loudly."""
    db_path = tmp_path / "never-created.db"

    with pytest.raises(FileNotFoundError, match=re.escape("never-created.db")):
        backup.run(db_path)


def test_backup_run_busy_wal_checkpoint_raises_not_stale_backup(
    tmp_path: Path,
) -> None:
    """T-3/T-5: a busy checkpoint fails loudly, writes no stale copy.

    With a write transaction held open by another connection, the WAL
    checkpoint cannot fold the sidecar; copying the main file then
    would silently miss committed pages. run() reports the busy
    checkpoint as a StoreError and writes nothing.
    """
    db_path = tmp_path / "rides.db"
    _create_store(db_path)
    holder = sqlite3.connect(str(db_path))
    try:
        holder.execute("PRAGMA journal_mode=WAL")
        holder.execute("BEGIN IMMEDIATE")
        holder.execute("CREATE TABLE held (x INTEGER)")

        with pytest.raises(StoreError, match=re.escape("wal_checkpoint busy")):
            backup.run(db_path)
    finally:
        holder.rollback()
        holder.close()

    assert _backup_files(db_path) == []


# -------------------------------------------------- schedule_hourly


def _recording_runner() -> tuple[Callable[[], None], list[int]]:
    """Return (runner, calls) -- runner records each invocation."""
    calls: list[int] = []

    def _runner() -> None:
        calls.append(1)

    return _runner, calls


def test_backup_hourly_first_tick_seeds_and_does_not_run() -> None:
    """The first tick only records the hour; no backup runs yet."""
    clock = _FakeClock(datetime(2026, 9, 20, 10, 0))  # noqa: DTZ001 -- fake wall clock
    runner, calls = _recording_runner()

    scheduler = backup.schedule_hourly(runner, clock=clock)
    ran = scheduler.tick()

    assert ran is False
    assert calls == []


def test_backup_hourly_ticks_within_same_hour_do_not_run() -> None:
    """T-4 boundary: 10:00..10:59 are one hour -- no backups."""
    clock = _FakeClock(datetime(2026, 9, 20, 10, 0))  # noqa: DTZ001 -- fake wall clock
    runner, calls = _recording_runner()
    scheduler = backup.schedule_hourly(runner, clock=clock)
    scheduler.tick()

    clock.current = datetime(2026, 9, 20, 10, 59)  # noqa: DTZ001 -- same hour
    ran = scheduler.tick()

    assert ran is False
    assert calls == []


def test_backup_hourly_new_hour_boundary_runs_exactly_once() -> None:
    """Crossing 11:00 runs the backup; same-hour ticks stay quiet."""
    clock = _FakeClock(datetime(2026, 9, 20, 10, 0))  # noqa: DTZ001 -- fake wall clock
    runner, calls = _recording_runner()
    scheduler = backup.schedule_hourly(runner, clock=clock)
    scheduler.tick()

    clock.current = datetime(2026, 9, 20, 11, 0)  # noqa: DTZ001 -- hour boundary
    ran = scheduler.tick()
    clock.current = datetime(2026, 9, 20, 11, 59)  # noqa: DTZ001 -- still 11:00's hour
    again = scheduler.tick()

    assert ran is True
    assert again is False
    assert calls == [1]


def test_backup_hourly_two_boundaries_run_twice(tmp_path: Path) -> None:
    """Two hour boundaries in a row trigger two backups, one each."""
    clock = _FakeClock(datetime(2026, 9, 20, 10, 0))  # noqa: DTZ001 -- fake wall clock
    db_path = tmp_path / "rides.db"
    _create_store(db_path)
    calls: list[str] = []

    def _runner() -> None:
        calls.append(str(backup.run(db_path, keep=20, clock=clock)))

    scheduler = backup.schedule_hourly(_runner, clock=clock)
    scheduler.tick()
    clock.current = datetime(2026, 9, 20, 11, 0)  # noqa: DTZ001
    scheduler.tick()
    clock.current = datetime(2026, 9, 20, 12, 0)  # noqa: DTZ001
    scheduler.tick()

    assert len(calls) == 2
    assert len(_backup_files(db_path)) == 2


# ---------------------------------------------------------- restore


def test_backup_restore_round_trip_returns_the_deleted_ride(
    tmp_path: Path,
) -> None:
    """Restore puts a backup's ride back over the live path."""
    db_path = tmp_path / "rides.db"
    _create_store(db_path, name="Original")
    backup_path = backup.run(db_path)

    store = Store.open(db_path)
    try:
        ride_id = store.rides()[0].id
        store.delete_ride(ride_id, "Original")
        assert store.rides() == []
    finally:
        store.close()

    backup.restore(backup_path, db_path)

    restored = Store.open(db_path)
    try:
        assert [ride.name for ride in restored.rides()] == ["Original"]
    finally:
        restored.close()


def test_backup_restore_missing_source_raises_naming_it(tmp_path: Path) -> None:
    """T-5: restoring a backup that does not exist fails loudly."""
    db_path = tmp_path / "rides.db"
    _create_store(db_path)
    missing = tmp_path / "nope.backups" / "nope.20260920-000000-000000.db"

    with pytest.raises(FileNotFoundError, match=re.escape("nope.20260920")):
        backup.restore(missing, db_path)


def test_backup_restore_clears_stale_wal_sidecars(tmp_path: Path) -> None:
    """Restore removes leftover -wal/-shm files beside the live path.

    The hazard restore exists to clear: a stale ``-wal``/``-shm`` pair
    left by a crashed session (or a WAL connection that never
    checkpointed) must never survive pairing with a fresh main file.
    The sidecars are planted directly -- SQLite auto-checkpoints a
    WAL on its last connection close, so a real connection cannot be
    relied on to leave one behind in a test.
    """
    db_path = tmp_path / "rides.db"
    _create_store(db_path)
    backup_path = backup.run(db_path)
    (db_path.parent / f"{db_path.name}-wal").write_bytes(b"stale")
    (db_path.parent / f"{db_path.name}-shm").write_bytes(b"stale")
    assert (db_path.parent / f"{db_path.name}-wal").exists()

    backup.restore(backup_path, db_path)

    assert not (db_path.parent / f"{db_path.name}-wal").exists()
    assert not (db_path.parent / f"{db_path.name}-shm").exists()


# ------------------------------------- rotation property (T-7, R-54)


@given(
    runs=st.integers(min_value=1, max_value=24),
    keep=st.integers(min_value=1, max_value=8),
)
@settings(
    max_examples=60,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
def test_backup_run_rotation_keeps_the_newest_min_runs_keep_files(
    tmp_path: Path, runs: int, keep: int
) -> None:
    """Invariant: the newest min(runs, keep) backups survive."""
    # One isolated directory per example: tmp_path persists across a
    # Hypothesis run, and leftover backups would break the count.
    db_path = tmp_path / f"case-{runs}-{keep}" / "rides.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    _create_store(db_path)
    clock = _FakeClock(datetime(2026, 9, 20, 10, 0))  # noqa: DTZ001 -- fake wall clock

    def _advance_and_run() -> Path:
        clock.advance()
        return backup.run(db_path, keep=keep, clock=clock)

    written = _write_backups(runs, _advance_and_run)

    files = _backup_files(db_path)
    expected_keep = min(runs, keep)
    assert len(files) == expected_keep
    # Newest-first vs creation order: reverse one side before comparing.
    assert files == list(reversed(written[-expected_keep:]))
