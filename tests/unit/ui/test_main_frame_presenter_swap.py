# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the console's presenter swap (E5.4.1, D3).

``MainFrame.set_presenter`` wires the entry/lifecycle controls and the
tick timer exactly ONCE per frame, however many rides are attached;
``clear_presenter`` detaches the presenter and stops the timer without
re-arming that one-time wiring. A Clear Ride followed by a library Open
must therefore swap the presenter references and restart the stopped
timer -- never rebind the controls or build a second timer, because a
duplicate ``wx.Bind`` delivers every later event twice.

Only what needs no window is pinned here: the two methods are driven as
unbound methods against a shell that owns just the state they read and
write, the same pattern ``test_main_frame_riders_list.py`` uses for the
console delegates.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from rivercrossing.ui.views import main_frame

if TYPE_CHECKING:
    from collections.abc import Callable


class _FakeTimer:
    """A ``wx.Timer`` double recording Stop/Start and its state."""

    def __init__(self) -> None:
        """Start stopped, with no recorded transitions."""
        self.running = False
        self.stops = 0
        self.starts = 0

    def Stop(self) -> None:  # noqa: N802 -- wx API name the double mirrors
        """Record one stop and clear the running state."""
        self.running = False
        self.stops += 1

    def Start(self, _milliseconds: int) -> None:  # noqa: N802 -- wx API name
        """Record one start and set the running state."""
        self.running = True
        self.starts += 1

    def IsRunning(self) -> bool:  # noqa: N802 -- wx API name
        """Return whether the timer is currently running."""
        return self.running


class _Presenter:
    """The one member the swap reads off a presenter."""

    def on_plate_entered(self, plate: str) -> None:
        """Store nothing; the swap only keeps the reference."""


class _SwapShell:
    """A ``MainFrame`` double owning only the swap's own state."""

    def __init__(self, *, wired: bool = True, timer: bool = True) -> None:
        """Seed the one-time flag, the timer double and the call log."""
        self._wired = wired
        self._presenter: object | None = None
        self._on_submit: Callable[[str], None] | None = None
        self._tick_timer = _FakeTimer() if timer else None
        self.wire_calls: list[str] = []

    def wire_entry(self, on_submit: Callable[[str], None]) -> None:
        """Record the one-time entry wiring."""
        self.wire_calls.append("wire_entry")
        self._on_submit = on_submit

    def wire_console(self, presenter: Any) -> None:  # noqa: ANN401 -- a double
        """Record the console wiring and start the timer."""
        self.wire_calls.append("wire_console")
        self._presenter = presenter
        if self._tick_timer is not None:
            self._tick_timer.Start(1000)


def _attach(shell: _SwapShell, presenter: _Presenter) -> None:
    """Drive the real ``set_presenter`` against *shell*."""
    main_frame.MainFrame.set_presenter(shell, presenter)


def _clear(shell: _SwapShell) -> None:
    """Drive the real ``clear_presenter`` against *shell*."""
    main_frame.MainFrame.clear_presenter(shell)


# ------------------------------------------------------- set_presenter


def test_set_presenter_given_a_first_attach_wires_entry_and_console_once() -> None:
    """W1: the first ride attach performs the one-time wiring."""
    shell = _SwapShell(wired=False)
    presenter = _Presenter()

    _attach(shell, presenter)

    assert shell.wire_calls == ["wire_entry", "wire_console"]
    assert shell._wired is True
    assert shell._presenter is presenter
    assert shell._on_submit == presenter.on_plate_entered


def test_set_presenter_given_a_second_presenter_swaps_references_without_rewiring() -> None:
    """E5.4.1: a later ride swaps the references, never rebinds."""
    shell = _SwapShell(wired=False)
    first, second = _Presenter(), _Presenter()
    _attach(shell, first)

    _attach(shell, second)

    assert shell.wire_calls == ["wire_entry", "wire_console"]
    assert shell._presenter is second
    assert shell._on_submit == second.on_plate_entered


def test_set_presenter_after_clear_presenter_swaps_without_rewiring() -> None:
    """D3: Clear Ride then Open must not rebind the controls.

    ``clear_presenter`` deliberately leaves the one-time sentinel set,
    so the next attach takes the swap path: the controls bind once per
    frame however many clear/open cycles the operator performs.
    """
    shell = _SwapShell(wired=False)
    first, second = _Presenter(), _Presenter()
    _attach(shell, first)
    _clear(shell)

    _attach(shell, second)

    assert shell.wire_calls == ["wire_entry", "wire_console"]
    assert shell._presenter is second
    assert shell._on_submit == second.on_plate_entered


def test_set_presenter_after_clear_presenter_restarts_the_stopped_timer() -> None:
    """D3: the stopped tick timer resumes on attach."""
    shell = _SwapShell(wired=False)
    _attach(shell, _Presenter())
    _clear(shell)
    assert shell._tick_timer is not None
    assert shell._tick_timer.running is False

    _attach(shell, _Presenter())

    assert shell._tick_timer.running is True
    assert shell._tick_timer.starts == 2  # one from the wiring, one resume


def test_set_presenter_after_clear_presenter_keeps_the_one_timer_object() -> None:
    """D3: re-attach reuses the timer; it never builds a second one."""
    shell = _SwapShell(wired=False)
    _attach(shell, _Presenter())
    timer = shell._tick_timer
    _clear(shell)

    _attach(shell, _Presenter())

    assert shell._tick_timer is timer


def test_set_presenter_given_a_running_timer_leaves_it_running() -> None:
    """A live console swapping rides never restarts its own timer."""
    shell = _SwapShell(wired=False)
    _attach(shell, _Presenter())

    _attach(shell, _Presenter())

    assert shell._tick_timer is not None
    assert shell._tick_timer.starts == 1


# ----------------------------------------------------- clear_presenter


def test_clear_presenter_given_a_live_presenter_stops_the_timer_and_detaches() -> None:
    """D3: the cleared console stops ticking and detaches."""
    shell = _SwapShell(wired=False)
    _attach(shell, _Presenter())

    _clear(shell)

    assert shell._tick_timer is not None
    assert shell._tick_timer.stops == 1
    assert shell._tick_timer.running is False
    assert shell._presenter is None
    assert shell._on_submit is None


def test_clear_presenter_keeps_the_one_time_wiring_sentinel_set() -> None:
    """D3: clearing a ride is a detach, not a de-wire."""
    shell = _SwapShell(wired=False)
    _attach(shell, _Presenter())

    _clear(shell)

    assert shell._wired is True


def test_clear_presenter_given_a_never_wired_console_is_a_noop() -> None:
    """A console that never wired a timer detaches without an error."""
    shell = _SwapShell(wired=False, timer=False)

    _clear(shell)

    assert shell._presenter is None
    assert shell._on_submit is None
