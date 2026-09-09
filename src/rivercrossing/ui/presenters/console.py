# SPDX-License-Identifier: GPL-3.0-only
"""Console presenter -- main_frame (1a), the live-timing screen.

``ConsoleView``/``ConsolePresenter`` are module-skeletons.md's
verbatim contract (ui.presenters section) -- names and signatures
below are binding, not derived -- grown by the members the live
presenter actually calls: ``set_stop_enabled`` (R-35's arm gate),
``set_hide_times`` (R-37), ``show_clock`` (the tick's elapsed
display), ``set_entry_locked`` (R-35's "only confirming locks the
entry field"), -- WS-D/WS-H -- ``set_clock_fractions`` (the
gauge-clock dials), ``show_flagged`` and ``show_riders`` (the review
notebook's two tabs), and -- W12 -- ``set_team_ui_visible`` (the
R-11 Teams-chip visibility on the count chips), the same "add the
member once the presenter calls it" precedent ``main_frame.py``'s
own docstring records.

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
  before finishing; the gate runs the real evaluator self-test
  (``hands.self_test()``) fresh on each finish.

W5 adds the console-button gates and the native-dialog seams:

- ``refresh_console_gates`` re-applies the Start/Undo button
  enablement from the engine (the mi_start_ride and mi_undo_crossing
  rules); the view calls it from every ``set_state``/``show_feed``
  render, so initial paints, console swaps and in-session transitions
  all land on the same verdicts.
- ``on_start`` surfaces ``StartBlockedError`` refusals through the
  view's native warning seam (``show_warning``) instead of the status
  notice -- a modal "Cannot Start Ride" alert with the engine's
  reason.
- ``on_stop_requested`` is the one shared Stop handler for the
  console Stop button and the Ride ▸ Stop Ride… menu row: a riderless
  roster gets a native warning (no Stop dialog at all), a RUNNING
  ride gets the native confirm (``view.confirm``), and a confirmed OK
  runs the unchanged ``on_stop_confirmed`` act-3 flow.

W6 adds the stopped-clock display freeze:

- ``_refresh_clock`` freezes the elapsed/remaining labels and the
  gauge dials while the engine is stopped (R-35's guard, state still
  RUNNING): the value is captured lazily on the first refresh that
  observes the stop, so a console rebuild while stopped re-captures
  on its next tick. The engine keeps spec.md's wall-clock elapsed
  (spec.md:38), so continue jumps the display forward and nothing is
  lost. The freeze is a display product decision; the design
  write-back lands in W15.
"""

from time import monotonic
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing import hands
from rivercrossing.ride import IllegalStateError, RideStatus, StartBlockedError
from rivercrossing.roster import EntryMode
from rivercrossing.ui.presenters.data_source import format_duration
from rivercrossing.ui.sound import Cue  # Re-exported; see module docstring

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ride import RideEngine
    from rivercrossing.ui.presenters.data_source import Counters, DataSource, FeedRow, RiderRow

__all__ = [
    "ARM_TIMEOUT_S",
    "FINISH_GATE",
    "ConsolePresenter",
    "ConsoleView",
    "Cue",
    "stop_light_mode",
]

# R-35: "Arm auto-clears after use or 10 s." The presenter's tick()
# disarms once this many seconds have passed since arming.
ARM_TIMEOUT_S = 10.0


def _finish_gate_clear() -> bool:
    """Return whether the evaluator self-test is green (R-44, E6.4.3).

    A fresh run each finish (not a cached launch result): the gate is
    only as honest as the suite's last pass.
    """
    return hands.self_test().passed


# E6.4.3 hook: the gate runs the real evaluator self-test suite
# (``hands.self_test()``) fresh on each finish (module docstring).
FINISH_GATE: Callable[[], bool] = _finish_gate_clear


def stop_light_mode(status: RideStatus) -> str:
    """Return the ride-status light's mode for *status* (WS-D).

    The console's code-side ``StopLight`` (``views/gauges.py``) lights
    one of three circles; this is the RideStatus -> mode mapping the
    view applies in ``set_state``. Pure (like ``_rejection_notice``),
    so the mapping is testable without wx. REOPENED shares DRAFT's
    amber: the corrections banner and the status label carry the
    distinction -- the light never carries meaning by colour alone.

    Returns:
        ``"green"`` RUNNING, ``"yellow"`` DRAFT/REOPENED, ``"red"``
        FINISHED.
    """
    if status is RideStatus.RUNNING:
        return "green"
    if status is RideStatus.FINISHED:
        return "red"
    return "yellow"


def _clock_fraction(seconds: float, total: float) -> float:
    """Return *seconds* as a 0.0..1.0 dial fraction of *total* (WS-D).

    Clamps at both ends: a ride past its planned duration shows a full
    elapsed dial and an empty remaining dial rather than overflowing.
    *total* is ``RideConfig.planned_duration_s``, which
    ``__post_init__`` validates positive -- no zero guard needed.
    """
    return min(1.0, max(0.0, seconds / total))


@runtime_checkable
class ConsoleView(Protocol):
    """View surface for the main console (main_frame, 1a)."""

    def show_feed(self, rows: list[FeedRow]) -> None:
        """Render the crossings feed, newest first."""
        ...

    def show_counters(self, c: Counters) -> None:
        """Render the six counter chips."""

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """Show or hide the Teams chip (R-11, W12).

        A solo-only ride hides the whole team UI, the console's Teams
        chip included; the presenter pushes the verdict from the
        engine's own ``config.entry_mode``, the same source
        commands.py's ``teams_allowed`` gate reads.
        """
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

    def set_clock_fractions(self, *, elapsed_frac: float, remaining_frac: float) -> None:
        """Drive the two gauge-clock dials (WS-D, R-30).

        Each fraction is the 0.0..1.0 hand position the view's
        ``RaceClock`` draws -- elapsed fills toward 1.0 as the ride
        runs, remaining drains toward 0.0.
        """
        ...

    def show_flagged(self, rows: list[FeedRow]) -> None:
        """Render the review notebook's flagged-crossing rows (WS-H)."""
        ...

    def show_riders(self, rows: list[RiderRow]) -> None:
        """Render the review notebook's riders rows (WS-H)."""
        ...

    def set_entry_locked(self, *, locked: bool) -> None:
        """Lock or unlock the plate entry row (R-35's stop lock)."""
        ...

    def set_start_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the Start ride button (W5 gate).

        The engine-backed verdict (DRAFT or stopped-RUNNING) comes
        from the presenter's ``refresh_console_gates``; the view only
        applies it.
        """
        ...

    def set_undo_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the Undo last button (W5 gate).

        The engine-backed verdict (RUNNING with >= 1 crossing) comes
        from the presenter's ``refresh_console_gates``; the view only
        applies it.
        """
        ...

    def show_warning(self, title: str, message: str) -> None:
        """Show *message* as a modal warning over this console (W5)."""
        ...

    def confirm(  # noqa: PLR0913 -- (title, message) + 2 button labels, mirroring std_dialogs.show_confirm
        self,
        title: str,
        message: str,
        *,
        ok_label: str,
        cancel_label: str,
    ) -> bool:
        """Ask a destructive confirm; return whether OK was chosen.

        The view owns the parent window and opens the native confirm
        (``ui.std_dialogs.show_confirm``); the presenter reads only
        the boolean verdict, so the flow stays headless-testable.
        """
        ...


class ConsolePresenter:
    """Presenter for the main console (main_frame, 1a).

    Holds ``(view, engine, source)``: the engine owns the write side
    (``record_crossing``/``undo_last``/``start``/``stop``/``finish``),
    the read-only ``DataSource`` serves feed/counters/status, and the
    view renders. ``now`` is the presenter's own monotonic clock seam
    for R-35's 10 s arm auto-clear -- injected in tests, defaulting to
    ``time.monotonic``.

    W12: the presenter renders the Teams chip's R-11 visibility at
    construction (:meth:`ConsoleView.set_team_ui_visible` from
    ``engine.config.entry_mode``) -- one push per presenter, because a
    ride's entry mode never changes and the app builds one presenter
    per ride/console-switch.
    """

    def __init__(  # noqa: PLR0913 -- S4 API (view, engine, source) + the testable now seam
        self,
        view: ConsoleView,
        engine: RideEngine,
        source: DataSource,
        *,
        now: Callable[[], float] | None = None,
    ) -> None:
        """Store the collaborators and render the chip visibility.

        Renders the initial console state the constructor owns (W12:
        the Teams chip's R-11 visibility from the engine's config
        mode), mirroring ``AddRiderPresenter``'s render-on-birth.

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
        # W6: the elapsed value shown while the engine is stopped;
        # None while live, so the first refresh after a stop captures.
        self._frozen_elapsed: float | None = None
        # W12/R-11: the constructor-owned render (class docstring).
        self.view.set_team_ui_visible(visible=self.engine.config.entry_mode is EntryMode.MIXED)

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
        """Handle Start Ride (start_btn / Ride ▸ Start Ride).

        ``engine.start()`` covers both DRAFT -> RUNNING and continue-
        after-stop; on success the console reflects RUNNING, unlocks
        the entry row and posts a notice. On continue the same call
        re-renders the clock (W6): the stop freeze clears and the
        display jumps to the live wall-clock elapsed without waiting
        for the next tick. A start the engine blocks
        because the ride is not ready (:class:`StartBlockedError`)
        surfaces as a modal warning naming the reason (W5); a state-
        machine refusal (finished ride) stays a status notice.
        """
        try:
            self.engine.start()
        except IllegalStateError as exc:
            self.view.show_notice(f"Cannot start: {exc}")
            return
        except StartBlockedError as exc:
            self.view.show_warning("Cannot Start Ride", f"Cannot start ride: {exc}")
            return
        self._refresh_feed()
        self._refresh_counters()
        self.view.set_state(self.engine.state)
        self.view.set_entry_locked(locked=False)
        self._refresh_clock()  # W6: continue unfreezes and shows live elapsed now
        self.view.show_notice("Ride started")

    def on_stop_requested(self) -> None:
        """Handle a Stop request: stop_btn or Ride ▸ Stop Ride… (W5).

        The one shared handler both entry points reach. A riderless
        roster (reachable after a replay against a drifted roster)
        gets a native warning and never a Stop dialog. A RUNNING ride
        asks the native confirm with the frozen stop copy, and a
        confirmed OK runs :meth:`on_stop_confirmed` -- the unchanged
        act-3 flow (engine stop, arm clear, entry lock, notice). A
        ride that is not RUNNING never shows the confirm; it falls
        through to the same engine-refusal notice ``on_stop_confirmed``
        posts (the controls are disabled outside RUNNING, so this arm
        is defensive).
        """
        if self.engine.entry_count == 0:
            self.view.show_warning("Cannot Stop Ride", "Cannot stop ride: roster has no riders")
            return
        if self.engine.state is not RideStatus.RUNNING:
            self.on_stop_confirmed()
            return
        if self.view.confirm(
            "Stop Ride?",
            "The clock stops for everyone. Riders still on course keep their laps; "
            "no cards are dealt after stop. You can continue the ride later "
            "without losing anything.",
            ok_label="Stop ride",
            cancel_label="Cancel",
        ):
            self.on_stop_confirmed()

    def refresh_console_gates(self) -> None:
        """Re-apply the console Start/Undo button gates from the engine.

        W5: ``start_btn`` mirrors the mi_start_ride rule -- enabled in
        DRAFT and in stopped-RUNNING (continue-after-stop is the
        resume mechanism), disabled in live RUNNING, FINISHED and
        REOPENED; ``undo_btn`` mirrors the mi_undo_crossing rule --
        enabled only in RUNNING with at least one crossing. The view
        calls this from every ``set_state``/``show_feed`` render
        (constructor, console swaps and presenter transitions alike),
        so the engine is the single source of truth for the buttons.
        """
        running = self.engine.state is RideStatus.RUNNING
        self.view.set_start_enabled(
            enabled=self.engine.state is RideStatus.DRAFT or (running and self.engine.stopped)
        )
        self.view.set_undo_enabled(enabled=running and len(self.engine.crossings) >= 1)

    def on_arm_stop(self, *, armed: bool) -> None:
        """Handle the arm_stop_chk toggle guarding stop_btn (R-35).

        Arming records the monotonic instant so ``tick`` can disarm
        after :data:`ARM_TIMEOUT_S`; the Stop button is enabled only
        while armed.
        """
        self._armed_at = self._now() if armed else None
        self.view.set_stop_enabled(enabled=armed)

    def on_stop_confirmed(self) -> None:
        """Handle a confirmed Stop (R-35, act 3).

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
        ``engine.finish()`` closes the shoe -- from RUNNING, or from
        REOPENED (E7.2.2's single primary "Finish again": re-locks to
        FINISHED after corrections, spec §3) -- and the console
        reflects FINISHED. Engine refusals (DRAFT) surface as notices.
        """
        if not FINISH_GATE():
            self.view.show_notice("Finish blocked: evaluator self-test did not pass")
            return
        was_reopened = self.engine.state is RideStatus.REOPENED
        try:
            self.engine.finish()
        except IllegalStateError as exc:
            self.view.show_notice(f"Cannot finish: {exc}")
            return
        self._refresh_feed()
        self._refresh_counters()
        self.view.set_state(self.engine.state)
        self.view.show_notice("Ride finished again" if was_reopened else "Ride finished")

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

        Refreshes the feed, review tabs, counters and clock from the
        source/engine (R-32/R-30, WS-D/WS-H), then expires the arm if
        its 10 s window lapsed (R-35) -- the presenter's own clock
        seam, no bare sleeps.
        """
        self._refresh_feed()
        self._refresh_riders()
        self._refresh_counters()
        self._refresh_clock()
        self._expire_arm()

    def _refresh_feed(self) -> None:
        """Re-render the crossings feed and its flagged subset.

        The flagged subset is the review notebook's "Needs Review" tab
        (WS-H): exactly the feed's R-34 flag rows, so a record/undo
        that changes the feed re-renders the flagged list in the same
        synchronous call.
        """
        rows = self.source.feed_rows()
        self.view.show_feed(rows)
        self.view.show_flagged([row for row in rows if row.flagged])

    def _refresh_riders(self) -> None:
        """Re-render the review notebook's riders tab from the source.

        The roster only changes when the app switches the console onto
        a store ride (E5.4.1), which routes through the presenter's
        source -- so refreshing here, on the periodic tick, keeps the
        tab current within one second of any such switch.
        """
        self.view.show_riders(self.source.riders())

    def _refresh_counters(self) -> None:
        """Re-render the six counter chips from the source."""
        self.view.show_counters(self.source.counters())

    def _refresh_clock(self) -> None:
        """Render the clock labels and gauge dials, or zeros pre-start.

        DRAFT has no ``actual_start`` yet: spec §13 says its clock
        shows the planned start, which the presenter cannot read
        before E5's store -- a zeroed clock (labels ``0:00:00`` and
        both dials at fraction 0.0) is this task's doc-silence
        (E5/E6 refine the DRAFT display). Once started, each dial's
        fraction is its seconds over ``planned_duration_s`` (WS-D), so
        the elapsed dial fills as the ride runs and the remaining dial
        drains -- the same clamp ``_clock_fraction`` applies at both
        ends keeps a ride past its planned duration on-scale.

        W6: while the engine is stopped (R-35's guard, state still
        RUNNING) the clock freezes at the elapsed value this refresh
        first observes after the stop -- labels and dials alike, so
        the numbers never advance across ticks. The freeze is lazy
        and presenter-local: a console rebuild over the same stopped
        engine re-captures on its first refresh, so library
        open/close round-trips land on the engine's live elapsed and
        then hold. The freeze is a display product decision that
        overrides the spec's wall-clock display rule (spec.md:38);
        the engine keeps counting underneath (R-30), so continue
        jumps the display forward and nothing is lost. The design
        write-back lands in W15 -- no engine change was wanted or
        made.
        """
        if self.engine.state is RideStatus.DRAFT:
            self.view.show_clock("0:00:00", "0:00:00")
            self.view.set_clock_fractions(elapsed_frac=0.0, remaining_frac=0.0)
            return
        if self.engine.stopped:
            frozen = self._frozen_elapsed
            if frozen is None:
                # First refresh that observes the stop: capture once,
                # then keep rendering that value until continue.
                frozen = self.engine.elapsed()
                self._frozen_elapsed = frozen
            elapsed = frozen
            remaining = max(0.0, float(self.engine.config.planned_duration_s) - elapsed)
        else:
            self._frozen_elapsed = None
            try:
                elapsed = self.engine.elapsed()
                remaining = self.engine.remaining()
            except IllegalStateError:
                # logic-coverage-exempt: T-3 -- the engine state machine
                # makes this arm unreachable: a started ride always
                # has an actual_start, and DRAFT (the only state
                # without one) returns above. Kept as the
                # pre-existing defensive twin of the DRAFT branch.
                self.view.show_clock("0:00:00", "0:00:00")
                self.view.set_clock_fractions(elapsed_frac=0.0, remaining_frac=0.0)
                return
            remaining = max(0.0, remaining)
        total = float(self.engine.config.planned_duration_s)
        self.view.show_clock(format_duration(elapsed), format_duration(remaining))
        self.view.set_clock_fractions(
            elapsed_frac=_clock_fraction(elapsed, total),
            remaining_frac=_clock_fraction(remaining, total),
        )

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
