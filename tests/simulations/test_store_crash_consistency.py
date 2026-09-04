# SPDX-License-Identifier: GPL-3.0-only
"""Crash-consistency suite for the store (E5.1.3, R-50).

R-50: "Every crossing/card/edit commits to SQLite (WAL) as it happens;
a crash or power loss loses at most the uncommitted keystroke." This
suite proves the WAL side of that contract the way the spec and the
task brief demand -- with a *hard-killed* subprocess, not a graceful
shutdown: :mod:`store_crash_child` (this directory) opens a real
:class:`~rivercrossing.store.Store`, creates a ride, and appends a
bounded stream of real :class:`~rivercrossing.ride.RideEngine` events
(one ``start`` plus ``_MAX_CROSSINGS`` ``record_crossing`` events, one
:meth:`~rivercrossing.store.Store.append` per event, each its own
committed transaction). The parent reads the child's stdout and kills
it with :meth:`subprocess.Popen.kill` -- SIGKILL on POSIX,
``TerminateProcess`` on Windows, the same cross-platform hard kill
``tests/functional/scenario_runner.py`` relies on -- at a seeded
random point, then reopens the same path and asserts R-50's invariant.

The child speaks a tiny line protocol on stdout:

- ``EVENT <json>``  -- printed *before* each append; ``<json>`` is the
  ``{"action": ..., "payload": {...}}`` the child is about to persist.
- ``CHECKPOINT <n>`` -- printed *after* append ``n`` commits. This is
  the child's committed floor: by the time the parent sees it, append
  ``n`` is durable.
- ``IN_TX`` -- mid-append mode only: printed while append ``n+1``'s
  transaction is open, between BEGIN and COMMIT.
- ``DONE <n>`` -- the child completed all ``n`` appends and exited.

The parent waits until the committed floor reaches a seeded kill
point (or it sees ``IN_TX`` / ``DONE``), then kills. Because the child
emits ``EVENT`` before each append and the parent parses *everything*
the child wrote before death -- including any lines that landed after
the kill decision but before the process died -- the captured stream
is the exact set of appends the child attempted. The invariant
asserted after every reopen is:

- ``PRAGMA integrity_check`` is ``ok`` -- the kill left no corruption;
- ``PRAGMA journal_mode`` is still ``wal`` -- WAL survived the crash;
- the one created ride survives, and the persisted audit log is
  exactly a prefix of the captured event stream, in order: nothing
  partial, nothing reordered, nothing beyond the child's own program
  (``len(persisted) <= _MAX_TOTAL``);
- ``len(persisted) >= CHECKPOINT floor`` -- nothing the child reported
  as committed is lost (the "at most the uncommitted keystroke" bound:
  a kill can only drop appends that were never committed);
- replaying the persisted log through
  :meth:`~rivercrossing.store.Store.load_engine` rebuilds a consistent
  :class:`~rivercrossing.ride.RideEngine` whose event list equals the
  persisted prefix.

The kill points come from a seeded RNG (``random.Random(seed)``), so
every run is byte-reproducible from the seed in the test id. The
50-kill loop is the brief's "50-kill loop green in CI". A
deterministic negative case kills the child mid-``append`` -- the
third event is written as an uncommitted audit row and the child
blocks with the transaction open (see :mod:`store_crash_child`) -- and
asserts WAL recovery rolls back exactly that uncommitted append while
the committed prefix survives intact.

No mocks anywhere: every assertion is a behavioral read of the
reopened database file.

Determinism: no bare sleeps in the parent; the child self-terminates
after a hard bound (``store_crash_child._CHILD_BOUND_SECONDS``) so a
hung child becomes a fast, named failure instead of a stall, and each
cycle is bounded by that bound plus ``_TERMINATE_WAIT_SECONDS``.
"""

import json
import random
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Any, cast

import pytest

from rivercrossing.ride import Event, RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store

# The crash child lives beside this suite; it is a separate interpreter
# process, never imported (tests/simulations carries no __init__.py).
_CRASH_CHILD = Path(__file__).with_name("store_crash_child.py")

# The child appends one start event plus _MAX_CROSSINGS crossings, so
# the whole stream is _MAX_TOTAL events. The shoe (1 deck, no jokers)
# holds 52 cards and never exhausts at 50 crossings, so no
# shoe_reshuffle event ever interleaves: the stream is exactly
# [start, crossing_1 .. crossing_50], all deterministically generated.
_MAX_CROSSINGS = 50
_MAX_TOTAL = _MAX_CROSSINGS + 1
_KILL_LOOP_ITERATIONS = 50  # the brief's 50-kill loop
_PLATE = "12"  # must match store_crash_child._PLATE: replay needs the same roster
_TERMINATE_WAIT_SECONDS = 5


@dataclass
class _ChildReport:
    """Everything the parent learned from one crash-child run.

    ``events`` is the child's emitted stream (every ``EVENT`` line the
    parent captured, including any that landed after the kill decision
    but before death), ``floor`` the highest ``CHECKPOINT`` seen, and
    ``saw_in_tx`` whether the mid-append child reported ``IN_TX``.
    ``stderr``/``returncode`` exist so a premature child death surfaces
    as a readable assertion failure instead of a mystery.
    """

    events: list[Event] = field(default_factory=list)
    floor: int = 0
    saw_in_tx: bool = False
    done_count: int | None = None
    returncode: int = -1
    stderr: str = ""


def _kill_point(seed: int) -> int:
    """Return the deterministic kill point for *seed*."""
    return random.Random(seed).randint(1, _MAX_TOTAL)  # noqa: S311 -- seeded test RNG, never secrets


def _parse_line(report: _ChildReport, line: str) -> None:
    """Fold one child stdout line into *report*.

    Recognizes the child's four protocol line kinds; anything else
    (a stray warning, say) is ignored so the protocol is forward
    tolerant.
    """
    if line.startswith("EVENT "):
        raw = json.loads(line[6:])
        report.events.append(Event(action=raw["action"], payload=dict(raw["payload"])))
    elif line.startswith("CHECKPOINT "):
        report.floor = max(report.floor, int(line[11:]))
    elif line == "IN_TX":
        report.saw_in_tx = True
    elif line.startswith("DONE "):
        report.done_count = int(line[5:])


def _spawn_child(path: Path, seed: int, *, die_mid_append: bool) -> subprocess.Popen[str]:
    """Spawn one fresh crash child against *path* with *seed*."""
    argv = [sys.executable, str(_CRASH_CHILD), str(path), str(_MAX_CROSSINGS), str(seed)]
    if die_mid_append:
        argv.append("--die-mid-append")
    return subprocess.Popen(  # noqa: S603 -- fixed argv: this interpreter + our own child + ints
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _kill_now(report: _ChildReport, kill_point: int | None) -> bool:
    """Return whether the parent should kill the child now.

    ``None`` means run to completion; ``0`` means the mid-append crash
    (kill only once the child reports its transaction is open);
    otherwise kill once the committed floor reaches *kill_point*.
    """
    if report.done_count is not None:
        return True
    if report.saw_in_tx:
        return True
    if kill_point is None or kill_point == 0:
        return False
    return report.floor >= kill_point


def _terminate(proc: subprocess.Popen[str], report: _ChildReport) -> None:
    """Kill *proc* if alive, reap it, then parse any late output.

    The late pass matters: the child can commit and emit a few more
    ``EVENT``/``CHECKPOINT`` lines between the parent's kill decision
    and the signal landing. Those appends are durable and part of the
    stream, so they must land in *report* for the prefix comparison.
    """
    if proc.poll() is None:
        proc.kill()
    proc.wait(timeout=_TERMINATE_WAIT_SECONDS)
    report.returncode = proc.returncode if proc.returncode is not None else -1
    if proc.stdout is not None:
        for line in proc.stdout:
            _parse_line(report, line.rstrip("\r\n"))


def _drain(stream: Any, sink: list[str]) -> None:  # noqa: ANN401
    """Append *stream*'s lines to *sink* until EOF or a closed pipe."""
    try:
        for line in stream:
            sink.append(line)  # noqa: PERF402 -- incremental drain; a killed child never EOFs normally
    except OSError, ValueError:
        # The parent's _terminate path can race the reader's read with
        # a closed pipe; that is not a failure, just the end of input.
        return


def _run_cycle(path: Path, seed: int, kill_point: int | None) -> _ChildReport:
    """Spawn the crash child and drive it to a kill or natural exit.

    Args:
        path: The database file the child will open.
        seed: Determinism seed for the child's shoe (and, for the kill
            loop, its kill point).
        kill_point: Committed floor to wait for before killing
            (1.._MAX_TOTAL); ``0`` selects the mid-append crash; None
            runs the child to completion.

    Returns:
        The parsed child report (see :class:`_ChildReport`).
    """
    proc = _spawn_child(path, seed, die_mid_append=(kill_point == 0))
    report = _ChildReport()
    stderr_lines: list[str] = []
    terr = threading.Thread(target=_drain, args=(proc.stderr, stderr_lines), daemon=True)
    terr.start()
    # _spawn_child always uses stdout=PIPE, so the wrapper is present;
    # cast narrows the type without a vacuous assertion.
    stdout = cast("IO[str]", proc.stdout)
    try:
        for line in stdout:
            _parse_line(report, line.rstrip("\r\n"))
            if _kill_now(report, kill_point):
                break
    finally:
        _terminate(proc, report)
    terr.join(timeout=2)
    report.stderr = "".join(stderr_lines)
    return report


def _replay_roster() -> Roster:
    """Build the crash child's roster for load_engine replay (E5.1.2).

    load_engine takes the roster from the caller -- full
    roster-from-DB reconstruction is E5.4.1 -- so the parent must hand
    it the same one plate the child created.
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Alice", last_name="", plate=_PLATE)
    return roster


def _assert_consistent_prefix(path: Path, report: _ChildReport) -> int:
    """Reopen *path*; assert R-50's prefix invariant; return the count.

    Asserts, in order: the crash left no corruption (``PRAGMA
    integrity_check`` is ``ok``) and WAL is still on; the one created
    ride survives; the persisted audit log is exactly a prefix of the
    stream the child emitted (nothing partial, nothing reordered,
    nothing beyond the child's program); at least the child's reported
    committed floor survives; and replaying the log through
    :meth:`~rivercrossing.store.Store.load_engine` rebuilds a
    consistent engine whose own event list equals the persisted
    prefix.
    """
    store = Store.open(path)
    try:
        rides = store.rides()
        assert len(rides) == 1, f"expected one ride after crash, got {rides!r}"
        ride_id = rides[0].id
        engine = store.load_engine(ride_id, _replay_roster())
    finally:
        store.close()
    with closing(sqlite3.connect(str(path))) as conn:
        conn.row_factory = sqlite3.Row
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        rows = conn.execute(
            "SELECT action, payload_json FROM audit WHERE ride_id = ? ORDER BY id",
            (ride_id,),
        ).fetchall()
    persisted = [
        Event(action=row["action"], payload=json.loads(row["payload_json"])) for row in rows
    ]

    captured = report.events
    assert len(persisted) >= report.floor, (
        f"committed events lost: persisted {len(persisted)} < floor {report.floor}\n"
        f"rc={report.returncode}\nstderr={report.stderr}"
    )
    assert len(persisted) <= _MAX_TOTAL, (
        f"persisted {len(persisted)} rows, beyond the child's program of {_MAX_TOTAL}"
    )
    assert persisted == captured[: len(persisted)], (
        "persisted stream is not a prefix of the child's emitted stream:\n"
        f"persisted={persisted}\ncaptured={captured}"
    )
    assert len(persisted) == len(engine.events), (
        f"audit rows {len(persisted)} != replayed events {len(engine.events)}"
    )
    assert engine.events == tuple(persisted), "replay rebuilt a different event stream"
    if persisted:
        assert engine.state is RideStatus.RUNNING
        assert len(engine.crossings) == len(persisted) - 1
    return len(persisted)


# ------------------------------------------------------------- tests


def test_crash_seeded_kill_points_deterministic_and_cover_both_ends() -> None:
    """The 50 seeded kill points vary and hit both stream boundaries.

    Guards the harness itself: if every kill point collapsed to one
    value the kill loop would be testing one crash position 50 times,
    and a kill at the very first commit (floor 1) or the very last
    (floor _MAX_TOTAL) must be among them for the loop to cover the
    range's edges.
    """
    points = [_kill_point(seed) for seed in range(_KILL_LOOP_ITERATIONS)]
    assert len(set(points)) >= 20, f"kill points too clustered: {sorted(set(points))}"
    assert min(points) == 1, f"kill point 1 (after the start event) never hit: {sorted(points)}"
    assert max(points) == _MAX_TOTAL, f"final-commit kill never hit: {sorted(points)}"


@pytest.mark.parametrize(
    "seed", range(_KILL_LOOP_ITERATIONS), ids=[f"seed-{s}" for s in range(_KILL_LOOP_ITERATIONS)]
)
def test_crash_kill_loop_reopen_keeps_committed_prefix(tmp_path: Path, seed: int) -> None:
    """A seeded kill leaves a consistent committed prefix."""
    db_path = tmp_path / f"crash-{seed}.db"
    kill_point = _kill_point(seed)
    report = _run_cycle(db_path, seed, kill_point)
    assert report.floor >= kill_point, (
        f"child never reached kill point {kill_point} (floor {report.floor}): "
        f"rc={report.returncode}\nstderr={report.stderr}"
    )
    _assert_consistent_prefix(db_path, report)


def test_crash_mid_append_kill_reopen_preserves_committed_prefix(tmp_path: Path) -> None:
    """A mid-append kill rolls back only the uncommitted append.

    The deterministic negative case: the child commits start and one
    crossing, then writes the third event as an uncommitted audit row
    and blocks with the transaction open (the sibling-connection
    mechanism in :mod:`store_crash_child`). WAL recovery on reopen
    must discard the uncommitted row and keep exactly the committed
    prefix.
    """
    db_path = tmp_path / "crash-mid-append.db"
    report = _run_cycle(db_path, seed=_KILL_LOOP_ITERATIONS, kill_point=0)
    assert report.saw_in_tx, (
        f"child never blocked mid-append: rc={report.returncode}\nstderr={report.stderr}"
    )
    count = _assert_consistent_prefix(db_path, report)
    assert count == report.floor, (
        f"mid-append crash kept {count} rows, committed floor was {report.floor}"
    )


def test_crash_child_natural_completion_persists_every_event(tmp_path: Path) -> None:
    """A child that completes persists the full stream, uncorrupted.

    The harness baseline: without any kill, every one of the child's
    appends must be present and replayable -- which also proves the
    ``EVENT``/``CHECKPOINT``/``DONE`` protocol and the parent's parser
    capture the whole stream.
    """
    db_path = tmp_path / "crash-complete.db"
    report = _run_cycle(db_path, seed=12_345, kill_point=None)
    assert report.done_count == _MAX_TOTAL, (
        f"child did not finish: rc={report.returncode}\nstderr={report.stderr}"
    )
    count = _assert_consistent_prefix(db_path, report)
    assert count == _MAX_TOTAL


def test_crash_child_missing_args_prints_usage_and_exits_2() -> None:
    """Invoking the crash child without args fails loudly, not silently.

    The child's argv guard is a harness branch (its usage contract
    with the parent); a missing argument must surface as exit code 2
    with a usage line, never a silent half-run.
    """
    completed = subprocess.run(  # noqa: S603 -- fixed argv: this interpreter + our own child
        [sys.executable, str(_CRASH_CHILD)],
        capture_output=True,
        text=True,
        timeout=_TERMINATE_WAIT_SECONDS,
        check=False,
    )
    assert completed.returncode == 2
    assert "usage:" in completed.stdout


def test_crash_child_mid_append_requires_enough_crossings(tmp_path: Path) -> None:
    """--die-mid-append with too few crossings refuses loudly (T-5).

    The child's precondition guard (``_MID_APPEND_BLOCK_AT`` needs the
    loop to reach it) raises ``ValueError``; a wrong invocation must
    fail with a traceback naming the reason, not block forever or
    silently ignore the flag.
    """
    completed = subprocess.run(  # noqa: S603 -- fixed argv: this interpreter + our own child
        [sys.executable, str(_CRASH_CHILD), str(tmp_path / "x.db"), "1", "0", "--die-mid-append"],
        capture_output=True,
        text=True,
        timeout=_TERMINATE_WAIT_SECONDS,
        check=False,
    )
    assert completed.returncode == 1
    assert "needs at least 2 crossings" in completed.stderr
