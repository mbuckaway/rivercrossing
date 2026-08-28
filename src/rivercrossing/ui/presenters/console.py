# SPDX-License-Identifier: GPL-3.0-only
"""Console presenter -- main_frame (1a), the live-timing screen.

``ConsoleView``/``ConsolePresenter`` are module-skeletons.md's
verbatim contract (ui.presenters section) -- names and signatures
below are binding, not derived -- grown by the four members the live
presenter actually calls: ``set_stop_enabled`` (R-35's arm gate),
``set_hide_times`` (R-37), ``show_clock`` (the tick's elapsed
display) and ``set_entry_locked`` (R-35's "only confirming locks the
entry field"), the same "add the member once the presenter calls it"
precedent ``main_frame.py``'s own docstring records.

Pure Python -- no ``wx`` import may ever land here (R-71). The
``Cue`` enum it re-exports lives in ``rivercrossing.ui.sound``
(E4.4.3), which is wx-lazy, so importing this module -- or the
presenters package -- still never loads wx (the no-wx import probe in
``tests/unit/presenters/test_protocols.py`` pins that).

E4.4.1-E4.4.3 behavior (spec §10/§13, R-31/32/34/35/37):

- ``on_plate_entered`` records through the engine. Accepted (or
  flagged) crossings refresh the feed/counters, flash the row, play
  the RECORDED (or FLAGGED) cue, clear the field and refocus.
  Rejections (``unknown_plate`` / not running / stopped) play the
  ERROR cue, post a notice, and **keep the field** -- R-31's "focus
  stays in the entry field" means a mistyped plate is corrected in
  place, never wiped.
- ``on_undo``/``on_stop_confirmed``/``on_start``/``on_finish`` drive
  the engine's write side; engine refusals surface as notices, never
  crashes.
- The arm/stop flow (R-35) owns the 10 s auto-clear through the
  presenter's own monotonic clock seam (``now``), driven by ``tick``
  -- testable with a fake tick, no bare sleeps.
- ``FINISH_GATE`` is the E6.4.3 hook: the finish flow consults it
  before finishing; the stub returns True (green) until E6.4.3 wires
  the real evaluator self-test.
"""

from time import monotonic
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing.ride import IllegalStateError, RideStatus, StartBlockedError
from rivercrossing.ui.presenters.data_source import format_duration
from rivercrossing.ui.sound import Cue  # re-exported; see module docstring

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ride import RideEngine
    from rivercrossing.ui.presenters.data_source import Counters, DataSource, FeedRow

__all__ = ["ARM_TIMEOUT_S", "FINISH_GATE", "ConsolePresenter", "ConsoleView", "Cue"]

# R-35: "Arm auto-clears after use or 10 s." The presenter's tick()
# disarms once this many seconds have passed since arming.
ARM_TIMEOUT_S = 10.0


def _finish_gate_clear() -> bool:
    """Return True: the stub gate stays green until E6.4.3."""
    return True


# E6.4.3 wires the real evaluator self-test report here; until then a
# stub returns True so the finish flow is green (task-briefs E4.4.2).
FINISH_GATE: Callable[[], bool] = _finish_gate_clear


@runtime_checkable
class ConsoleView(Protocol):
    """View surface for the main console (main_frame, 1a)."""

    def show_feed(self, rows: list[FeedRow]) -> None:
        """Render the crossings feed, newest first."""
        ...

    def show_counters(self, c: Counters) -> None:
        """Render the four counter chips."""
        ...

    def flash_crossing(self, r: FeedRow) -> None:
        """Highlight the just-recorded crossing (last_crossing_lbl)."""
        ...

    def set_state(self, status: RideStatus) -> None:
        """Reflect the ride's lifecycle state (clock, entry, banner)."""
        ...

    def focus_entry(self) -> None:
        """Return keyboard focus to the plate entry field."""
        ...

    def show_notice(self, text: str) -> None:
        """Show a transient notice (the status bar's first field)."""
        ...

    def clear_entry(self) -> None:
        """Empty the plate entry field."""
        ...

    def play(self, cue: Cue) -> None:
        """Play the audio cue for the given event."""
        ...

    def set_stop_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the Stop button (R-35 arm gate)."""
        ...

    def set_hide_times(self, *, hide: bool) -> None:
        """Toggle the Lap time/Total columns (R-37)."""
        ...

    def show_clock(self, elapsed: str, remaining: str) -> None:
        """Render the ride clock's elapsed/remaining labels (R-30)."""
        ...

    def set_entry_locked(self, *, locked: bool) -> None:
        """Lock or unlock the plate entry row (R-35's stop lock)."""
        ...


class ConsolePresenter:
    """Presenter for the main console (main_frame, 1a).

    Holds ``(view, engine, source)``: the engine owns the write side
    (``record_crossing``/``undo_last``/``start``/``stop``/``finish``),
    the read-only ``DataSource`` serves feed/counters/status, and the
    view renders. ``now`` is the presenter's own monotonic clock seam
    for R-35's 10 s arm auto-clear -- injected in tests, defaulting to
    ``time.monotonic``.
    """

    def __init__(  # noqa: PLR0913 -- S4 API (view, engine, source) + the testable now seam
        self,
        view: ConsoleView,
        engine: RideEngine,
        source: DataSource,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        """Store the view, engine and data source this presenter drives.

        Args:
            view: The console view to render into.
            engine: The ride engine (the write side).
            source: The read-only display-data seam.
            now: Monotonic clock for the arm timeout; defaults to
                ``time.monotonic``.
        """
        self.view = view
        self.engine = engine
        self.source = source
        self._now = now if now is not None else monotonic
        self._armed_at: float | None = None

    def on_plate_entered(self, text: str) -> None:
        """Handle Enter (or Record) with the plate entry's text.

        A blank/whitespace-only submission only returns focus (A3).
        Otherwise the plate goes to ``engine.record_crossing``:
        accepted crossings refresh the feed and counters, flash the
        new row, play RECORDED (or FLAGGED for a short lap, R-34),
        clear the field and refocus; refusals play ERROR, post a
        notice, and keep the field (R-31 -- pin).
        """
        plate = text.strip()
        if not plate:
            self.view.focus_entry()
            return
        result = self.engine.record_crossing(plate)
        if not result.accepted:
            self.view.play(Cue.ERROR)
            self.view.show_notice(_rejection_notice(plate, result.reason))
            self.view.focus_entry()
            return
        self._refresh_feed()
        self._refresh_counters()
        self.view.flash_crossing(self.source.feed_rows()[0])
        self.view.play(Cue.FLAGGED if result.flagged else Cue.RECORDED)
        self.view.clear_entry()
        self.view.focus_entry()

    def on_undo(self) -> None:
        """Handle Undo last (Ctrl+Z / undo_btn / mi_undo_crossing).

        Removes the newest crossing, refreshes the feed and counters,
        and posts a notice; an illegal undo (nothing to undo, wrong
        state) is caught and surfaced as a notice, never a crash.
        """
        try:
            self.engine.undo_last()
        except IllegalStateError as exc:
            self.view.show_notice(f"Undo unavailable: {exc}")
            return
        self._refresh_feed()
        self._refresh_counters()
        self.view.show_notice("Last crossing undone")

    def on_start(self) -> None:
        """Handle Start Ride (start_btn).

        ``engine.start()`` covers both DRAFT -> RUNNING and continue-
        after-stop; on success the console reflects RUNNING, unlocks
        the entry row and posts a notice. Engine refusals (finished
        ride, roster not ready) surface as notices.
        """
        try:
            self.engine.start()
        except (IllegalStateError, StartBlockedError) as exc:
            self.view.show_notice(f"Cannot start: {exc}")
            return
        self._refresh_feed()
        self._refresh_counters()
        self.view.set_state(self.engine.state)
        self.view.set_entry_locked(locked=False)
        self.view.show_notice("Ride started")

    def on_arm_stop(self, *, armed: bool) -> None:
        """Handle the arm_stop_chk toggle guarding stop_btn (R-35).

        Arming records the monotonic instant so ``tick`` can disarm
        after :data:`ARM_TIMEOUT_S`; the Stop button is enabled only
        while armed.
        """
        self._armed_at = self._now() if armed else None
        self.view.set_stop_enabled(enabled=armed)

    def on_stop_confirmed(self) -> None:
        """Handle confirmation from stop_confirm_dlg (R-35, act 3).

        ``engine.stop()`` locks plate entry while the ride stays
        RUNNING; the arm auto-clears after use. An illegal stop (not
        RUNNING, already stopped) is caught and surfaced as a notice.
        """
        try:
            self.engine.stop()
        except IllegalStateError as exc:
            self.view.show_notice(f"Cannot stop: {exc}")
            return
        self._armed_at = None
        self.view.set_stop_enabled(enabled=False)
        self.view.set_state(self.engine.state)
        self.view.set_entry_locked(locked=True)
        self.view.show_notice("Ride stopped — continue to resume")

    def on_hide_times(self, *, hide: bool) -> None:
        """Handle the hide-times setting toggling live (R-37)."""
        self.view.set_hide_times(hide=hide)

    def on_finish(self) -> None:
        """Handle the Finish Ride flow (E4.4.2, gate hook E6.4.3).

        Consults :data:`FINISH_GATE` first: a failing evaluator
        self-test blocks finishing with a notice. When clear,
        ``engine.finish()`` closes the shoe and the console reflects
        FINISHED. Engine refusals (DRAFT) surface as notices.
        """
        if not FINISH_GATE():
            self.view.show_notice("Finish blocked: evaluator self-test did not pass")
            return
        try:
            self.engine.finish()
        except IllegalStateError as exc:
            self.view.show_notice(f"Cannot finish: {exc}")
            return
        self._refresh_feed()
        self._refresh_counters()
        self.view.set_state(self.engine.state)
        self.view.show_notice("Ride finished")

    def on_reopen(self) -> None:
        """Handle Ride ▸ Reopen Ride (E5.4.1, spec §3).

        ``engine.reopen()`` moves a FINISHED ride into REOPENED -- the
        corrections-only state (clock closed, entry locked). The
        console reflects the new state (which shows the reopened
        corrections banner, R-36) and posts a notice; engine refusals
        (not FINISHED) surface as notices. The reopen event's
        persistence belongs to E5.4's async writer, not this task.
        """
        try:
            self.engine.reopen()
        except IllegalStateError as exc:
            self.view.show_notice(f"Cannot reopen: {exc}")
            return
        self._refresh_feed()
        self._refresh_counters()
        self.view.set_state(self.engine.state)
        self.view.show_notice("Ride reopened for corrections")

    def tick(self) -> None:
        """Handle a periodic clock/feed refresh tick.

        Refreshes the feed, counters and clock from the source/engine
        (R-32/R-30), then expires the arm if its 10 s window lapsed
        (R-35) -- the presenter's own clock seam, no bare sleeps.
        """
        self._refresh_feed()
        self._refresh_counters()
        self._refresh_clock()
        self._expire_arm()

    def _refresh_feed(self) -> None:
        """Re-render the crossings feed from the source."""
        self.view.show_feed(self.source.feed_rows())

    def _refresh_counters(self) -> None:
        """Re-render the four counter chips from the source."""
        self.view.show_counters(self.source.counters())

    def _refresh_clock(self) -> None:
        """Render the elapsed/remaining clock, or zeros before start.

        DRAFT has no ``actual_start`` yet: spec §13 says its clock
        shows the planned start, which the presenter cannot read
        before E5's store -- a zeroed clock is this task's doc-silence
        (E5/E6 refine the DRAFT display).
        """
        if self.engine.state is RideStatus.DRAFT:
            self.view.show_clock("0:00:00", "0:00:00")
            return
        try:
            elapsed = self.engine.elapsed()
            remaining = self.engine.remaining()
        except IllegalStateError:
            self.view.show_clock("0:00:00", "0:00:00")
            return
        self.view.show_clock(format_duration(elapsed), format_duration(max(0.0, remaining)))

    def _expire_arm(self) -> None:
        """Disarm the stop arm once its 10 s window lapsed (R-35)."""
        if self._armed_at is not None and self._now() - self._armed_at >= ARM_TIMEOUT_S:
            self._armed_at = None
            self.view.set_stop_enabled(enabled=False)


def _rejection_notice(plate: str, reason: str | None) -> str:
    """Return the user-facing notice for a refused crossing.

    Maps the engine's machine-readable refusal reasons (ride.py) to
    console copy; an unknown reason still names the plate and reason
    rather than silently succeeding.
    """
    if reason == "unknown_plate":
        return f"Unknown plate {plate}"
    if reason == "ride is not running":
        return "The ride is not running"
    if reason == "ride is stopped":
        return "The ride is stopped"
    return f"Plate rejected: {reason}"
