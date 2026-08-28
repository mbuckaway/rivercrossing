# SPDX-License-Identifier: GPL-3.0-only
"""Database backups: open + hourly + manual, keep 20 (E5.3.1, R-54).

:func:`run` copies a SQLite database to a timestamped backup and
prunes to the newest ``keep`` (rotation -- at keep+1 the oldest
goes); :func:`schedule_hourly` is the minimal scheduler seam E5.4
drives with a real timer; :func:`restore` copies a backup back over a
live path. ``store`` stays wx-free: this module uses only the stdlib.

Doc-silence resolutions (recorded here because the spec leaves them
open and later EPICs build on them):

- **Backup location**: backups live in a sibling directory named
  ``<db>.backups/`` (for ``rides.db`` that is ``rides.db.backups/``
  beside it). Each backup is one file named
  ``<stem>.<YYYYmmdd-HHMMSS-ffffff>.db`` -- a UTC-rendered timestamp
  from the injected clock (the app's real clock is
  ``datetime.now(UTC)``), zero-padded and fixed-width, so a plain
  filename sort is a chronological sort and rotation needs no
  metadata.
- **WAL handling**: the R-50 store runs ``journal_mode=WAL``, so
  committed-but-not-yet-checkpointed pages live in the ``-wal``
  sidecar and a bare main-file copy would silently miss them.
  :func:`run` opens a short-lived second connection and runs
  ``PRAGMA wal_checkpoint(TRUNCATE)`` before copying -- folding the
  WAL into the main file -- so the copied main file alone is a
  complete, consistent snapshot; ``-wal``/``-shm`` are never copied.
  The store's own connection is idle at backup time (no open
  transaction), so the checkpoint from a second connection succeeds;
  a busy checkpoint raises rather than writing a silently stale
  backup. :func:`restore` removes stale ``-wal``/``-shm`` sidecars
  after copying, because a fresh main file must never be paired with
  a WAL written against the old one; the caller must have the live
  store closed.
- **keep floor**: ``keep`` is a positive integer -- a "keep 0
  backups" policy is meaningless, and ``keep < 1`` raises
  ``ValueError`` rather than silently pruning everything including
  the backup just written.
- **hourly while running**: R-54's "on open" backup and the manual
  Back Up Now are both plain :func:`run` calls; :func:`schedule_hourly`
  is only the hourly seam. Its first ``tick()`` seeds the last-seen
  hour and does not run (the open backup is a separate call); a
  later tick whose hour has strictly advanced runs the runner exactly
  once. E5.4 wires the app's real timer to ``tick()``.
"""

import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from rivercrossing.store.migrations import StoreError

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = [
    "BACKUP_SUFFIX",
    "DEFAULT_KEEP",
    "HourlyBackup",
    "backup_dir_for",
    "restore",
    "run",
    "schedule_hourly",
]

# R-54's keep-20 cap, the default every caller (manual backup,
# delete-before-backup, hourly) uses unless told otherwise.
DEFAULT_KEEP = 20

# The suffix every backup file carries; rotation globs on it.
BACKUP_SUFFIX = ".db"

# The one place the backup-location rule lives: a sibling directory
# named after the database file itself, so pruning and restore can
# never drift from where run() writes.
_DIR_SUFFIX = ".backups"

# The fixed-width, sortable timestamp every backup filename embeds.
_TIMESTAMP_FORMAT = "%Y%m%d-%H%M%S-%f"


def backup_dir_for(db_path: str | Path) -> Path:
    """Return the sibling backup directory for *db_path*.

    The one location rule: backups of ``/path/rides.db`` live in
    ``/path/rides.db.backups/``. The directory is returned whether or
    not it exists yet -- :func:`run` creates it on first use, and the
    tests' prune/restore helpers read it the same way.
    """
    path = Path(db_path)
    return path.parent / f"{path.name}{_DIR_SUFFIX}"


def _timestamp(clock: Callable[[], datetime]) -> str:
    """Render *clock*'s instant in the backup filename format."""
    return clock().strftime(_TIMESTAMP_FORMAT)


def _checkpoint(path: Path) -> None:
    """Fold a WAL database's committed pages into its main file.

    Opens a short-lived second connection and runs
    ``PRAGMA wal_checkpoint(TRUNCATE)``. A non-WAL database reports an
    empty checkpoint -- a no-op. A busy checkpoint (the store's own
    connection holding a transaction, which this app never does at
    backup time) raises rather than let a silently stale main file be
    copied as the backup.

    Raises:
        StoreError: If the checkpoint reports busy frames (another
            connection holds an open transaction).
    """
    conn = sqlite3.connect(str(path))
    try:
        row = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        busy = int(row[0]) if row is not None else 0
        if busy:
            raise StoreError(
                f"wal_checkpoint busy ({busy} frames not checkpointed); "
                "another connection holds a transaction on the database"
            )
    finally:
        conn.close()


def _prune(directory: Path, keep: int) -> None:
    """Delete all but the newest ``keep`` backups in *directory*.

    Filenames are the zero-padded UTC timestamps :func:`run` writes,
    so a reverse lexicographic sort is newest-first; the slice
    ``[keep:]`` is exactly the oldest surplus. Called after the new
    backup lands, so at keep+1 the oldest goes and ``keep`` remain.
    """
    for old in sorted(directory.glob(f"*{BACKUP_SUFFIX}"), reverse=True)[keep:]:
        old.unlink()


def _fsync(path: Path) -> None:
    """Force *path*'s bytes to disk (a crash leaves no half-copy)."""
    with path.open("r+b") as handle:
        os.fsync(handle.fileno())


def run(
    db_path: str | Path,
    keep: int = DEFAULT_KEEP,
    *,
    clock: Callable[[], datetime] = datetime.now,
) -> Path:
    """Copy *db_path* to a timestamped backup, then prune to ``keep``.

    Backs up a live or closed store: the checkpoint step works from a
    second connection, and the store's own connection is idle at
    backup time. Returns the new backup's path.

    Args:
        db_path: The SQLite file to back up (the store's main file).
        keep: How many backups to retain; the oldest beyond this is
            pruned (R-54's default is 20).
        clock: Wall-clock source for the filename timestamp; defaults
            to ``datetime.now`` (UTC when the app supplies it). Tests
            inject a fake clock for deterministic rotation.

    Returns:
        The path of the backup just written.

    Raises:
        FileNotFoundError: If *db_path* is not an existing file.
        ValueError: If *keep* is below 1.
        StoreError: If the WAL checkpoint cannot complete (a busy
            database -- see :func:`_checkpoint`).
    """
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"no database file to back up: {path}")
    if keep < 1:
        raise ValueError("keep must be at least 1")
    _checkpoint(path)

    directory = backup_dir_for(path)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{path.stem}.{_timestamp(clock)}{BACKUP_SUFFIX}"
    shutil.copy2(path, target)
    _fsync(target)
    _prune(directory, keep)
    return target


def restore(src: str | Path, dst: str | Path) -> None:
    """Copy backup *src* back over the live path *dst*.

    Removes stale ``-wal``/``-shm`` sidecars beside *dst* so a fresh
    main file is never paired with a WAL written against the old one.
    The live store must be closed when restoring.

    Args:
        src: The backup file to restore.
        dst: The live database path to overwrite.

    Raises:
        FileNotFoundError: If *src* does not exist.
    """
    source = Path(src)
    if not source.is_file():
        raise FileNotFoundError(f"backup file not found: {source}")
    destination = Path(dst)
    shutil.copy2(source, destination)
    _fsync(destination)
    for suffix in ("-wal", "-shm"):
        sidecar = destination.parent / f"{destination.name}{suffix}"
        if sidecar.exists():
            sidecar.unlink()


class HourlyBackup:
    """The hourly scheduler seam E5.4 drives with a real timer (R-54).

    Holds the last-seen hour and the injected clock; each ``tick()``
    runs the configured runner exactly once per hour boundary crossed
    while the app is running. The first tick only seeds the hour --
    R-54's "on open" backup is a separate :func:`run` call, never
    this tick.
    """

    def __init__(
        self,
        runner: Callable[[], None],
        *,
        clock: Callable[[], datetime] = datetime.now,
    ) -> None:
        """Record the runner and clock; no hour is seen yet.

        Args:
            runner: The zero-arg callable that writes one backup
                (typically ``lambda: backup.run(db_path)``).
            clock: Wall-clock source; tests inject a fake clock.
        """
        self._runner = runner
        self._clock = clock
        self._last_hour: datetime | None = None

    def tick(self) -> bool:
        """Check the clock and run the backup once per new hour.

        Returns:
            ``True`` when this tick ran the backup; ``False`` when it
            only seeded the hour or the hour has not advanced.
        """
        hour = self._clock().replace(minute=0, second=0, microsecond=0)
        if self._last_hour is None:
            self._last_hour = hour
            return False
        if hour <= self._last_hour:
            return False
        self._last_hour = hour
        self._runner()
        return True


def schedule_hourly(
    runner: Callable[[], None],
    *,
    clock: Callable[[], datetime] = datetime.now,
) -> HourlyBackup:
    """Return an :class:`HourlyBackup` the caller ticks each timer fire.

    Args:
        runner: The zero-arg callable that writes one backup.
        clock: Wall-clock source; defaults to ``datetime.now`` and is
            injectable for tests.

    Returns:
        A new :class:`HourlyBackup` instance.
    """
    return HourlyBackup(runner, clock=clock)
