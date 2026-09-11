# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for H2's native-dialog route rewiring.

Phase 11 replaced four XRC confirm dialogs with the code-side native
dialogs in :mod:`rivercrossing.ui.std_dialogs`:

* Ride ▸ Finish Ride… -> ``show_danger`` with the dynamic
  ``Finish ride`` / ``Finish again`` label (spec §3 design 8c).
* File ▸ Duplicate Ride… and Ride ▸ Reopen Ride -> ``show_prompt``.
* File ▸ Exit with no RUNNING ride -> ``show_confirm``.

The three ``ROUTE_TABLE`` rows move from ``DIALOG`` targets to
``COMMAND`` targets, so ``_make_route_handler`` dispatches them by
target. The native dialogs are the GUI I/O boundary (T-10), so each
test swaps the specific ``std_dialogs`` function for a recorder and
asserts both the copy the route passes and the action the confirmed
result triggers. No wx window is ever constructed.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING

import wx

from rivercrossing.ride import RideStatus
from rivercrossing.ui import app as app_module
from rivercrossing.ui import quit_flow, std_dialogs
from rivercrossing.ui.views import dialogs

if TYPE_CHECKING:
    import pytest


class _FakeFrame:
    """Record status-bar notices; no wx window ever exists."""

    def __init__(self) -> None:
        """Start with an empty notice log."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one status-bar notice."""
        self.notices.append(text)


class _FakeEngine:
    """The two engine facts the rewired routes read."""

    def __init__(self, state: RideStatus, name: str = "GORBA EPIC 2026") -> None:
        """Record the ride's lifecycle state and its display name."""
        self.state = state
        self.config = SimpleNamespace(name=name)


class _FakePresenter:
    """Record the finish/reopen actions the routes fire."""

    def __init__(self, engine: _FakeEngine) -> None:
        """Thread *engine* in; start with an empty action log."""
        self.engine = engine
        self.actions: list[str] = []

    def on_finish(self) -> None:
        """Record the finish action."""
        self.actions.append("finish")

    def on_reopen(self) -> None:
        """Record the reopen action."""
        self.actions.append("reopen")


class _FakeStore:
    """A store whose ride list grows when a ride is duplicated."""

    def __init__(self, rides: list[SimpleNamespace] | None = None) -> None:
        """Start with *rides* (a single DRAFT ride by default)."""
        self._rides = list(rides) if rides is not None else [SimpleNamespace(id=3, name="Ride A")]
        self.duplicated: list[int] = []
        self.close_session_calls = 0

    def rides(self) -> list[SimpleNamespace]:
        """Return the current ride rows."""
        return list(self._rides)

    def duplicate_ride(self, ride_id: int) -> int:
        """Record the source id and append the derived copy."""
        self.duplicated.append(ride_id)
        self._rides.append(SimpleNamespace(id=99, name="Ride A (copy)"))
        return 99

    def close_session(self) -> None:
        """Record the R-52 clean-quit stamp."""
        self.close_session_calls += 1


def _context(
    *,
    store: object = None,
    presenter: _FakePresenter | None = None,
) -> app_module._RouteContext:
    """Build a route context with a fake frame and no wx resource."""
    context = app_module._RouteContext(
        frame=_FakeFrame(),
        resource=None,
        roster=None,  # type: ignore[arg-type] -- these routes never touch it
        app=None,
        theme_controller=None,
        store=store,  # type: ignore[arg-type]
    )
    context.presenter = presenter  # type: ignore[assignment]
    return context


def _stub_show(
    monkeypatch: pytest.MonkeyPatch, name: str, result: int
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    """Swap ``std_dialogs.<name>`` for a recorder returning *result*."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def _show(*args: object, **kwargs: object) -> int:
        calls.append((args, kwargs))
        return result

    monkeypatch.setattr(std_dialogs, name, _show)
    return calls


# ---------------------------------------------------- Finish Ride…


def test_handle_finish_route_confirmed_danger_finishes_the_ride(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirmed Finish fires the live presenter's on_finish."""
    presenter = _FakePresenter(_FakeEngine(RideStatus.RUNNING))
    context = _context(presenter=presenter)
    calls = _stub_show(monkeypatch, "show_danger", wx.ID_OK)

    app_module._handle_finish_route(context)

    parent, title, message, ok_label, cancel_label = calls[0][0]
    assert (parent, title, ok_label, cancel_label) == (
        context.frame,
        "Finish Ride?",
        "Finish ride",
        "Cancel",
    )
    assert "reopen" in message.lower()
    assert presenter.actions == ["finish"]


def test_handle_finish_route_reopened_ride_offers_finish_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E7.2.2: a REOPENED ride is finished again, labelled properly."""
    presenter = _FakePresenter(_FakeEngine(RideStatus.REOPENED))
    context = _context(presenter=presenter)
    calls = _stub_show(monkeypatch, "show_danger", wx.ID_OK)

    app_module._handle_finish_route(context)

    _parent, title, _message, ok_label, _cancel_label = calls[0][0]
    assert (title, ok_label) == dialogs.finish_again_labels()
    assert presenter.actions == ["finish"]


def test_handle_finish_route_cancelled_danger_does_not_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-35: cancelling the finish confirm finishes nothing."""
    presenter = _FakePresenter(_FakeEngine(RideStatus.RUNNING))
    context = _context(presenter=presenter)
    _stub_show(monkeypatch, "show_danger", wx.ID_CANCEL)

    app_module._handle_finish_route(context)

    assert presenter.actions == []


def test_handle_finish_route_without_a_presenter_posts_the_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No live ride: a confirmed Finish posts the stub notice."""
    context = _context(presenter=None)
    _stub_show(monkeypatch, "show_danger", wx.ID_OK)

    app_module._handle_finish_route(context)

    assert context.frame.notices == ["Finish Ride… — not yet implemented"]


# -------------------------------------- Duplicate Ride… / Reopen Ride


def test_handle_duplicate_ride_route_confirmed_prompt_duplicates_the_ride(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirmed prompt runs Store.duplicate_ride on the open ride."""
    store = _FakeStore()
    context = _context(store=store, presenter=_FakePresenter(_FakeEngine(RideStatus.DRAFT)))
    context.active_ride_id = 3
    calls = _stub_show(monkeypatch, "show_prompt", wx.ID_OK)

    app_module._handle_duplicate_ride_route(context)

    parent, title, message, ok_label, cancel_label = calls[0][0]
    assert (parent, title, ok_label, cancel_label) == (
        context.frame,
        "Duplicate Ride",
        "Duplicate",
        "Cancel",
    )
    assert "Ride A" in message
    assert "no timing data" in message
    assert store.duplicated == [3]
    assert context.frame.notices == ["Duplicated as Ride A (copy)"]


def test_handle_duplicate_ride_route_cancelled_prompt_leaves_no_copy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel writes nothing and posts nothing."""
    store = _FakeStore()
    context = _context(store=store, presenter=_FakePresenter(_FakeEngine(RideStatus.DRAFT)))
    context.active_ride_id = 3
    _stub_show(monkeypatch, "show_prompt", wx.ID_CANCEL)

    app_module._handle_duplicate_ride_route(context)

    assert store.duplicated == []
    assert context.frame.notices == []


def test_handle_reopen_ride_route_confirmed_prompt_fires_on_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirmed prompt moves the FINISHED ride back to REOPENED."""
    presenter = _FakePresenter(_FakeEngine(RideStatus.FINISHED))
    context = _context(presenter=presenter)
    calls = _stub_show(monkeypatch, "show_prompt", wx.ID_OK)

    app_module._handle_reopen_ride_route(context)

    parent, title, message, ok_label, cancel_label = calls[0][0]
    assert (parent, title, ok_label, cancel_label) == (
        context.frame,
        "Reopen Ride",
        "Reopen",
        "Cancel",
    )
    assert "GORBA EPIC 2026" in message
    assert "recompute" in message
    assert presenter.actions == ["reopen"]


def test_handle_reopen_ride_route_cancelled_prompt_does_not_reopen(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel leaves the ride FINISHED."""
    presenter = _FakePresenter(_FakeEngine(RideStatus.FINISHED))
    context = _context(presenter=presenter)
    _stub_show(monkeypatch, "show_prompt", wx.ID_CANCEL)

    app_module._handle_reopen_ride_route(context)

    assert presenter.actions == []


def test_handle_reopen_ride_route_without_a_presenter_posts_the_notice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No live ride: a confirmed Reopen posts the stub notice."""
    context = _context(presenter=None)
    calls = _stub_show(monkeypatch, "show_prompt", wx.ID_OK)

    app_module._handle_reopen_ride_route(context)

    assert "Reopen Ride" in calls[0][0][2]
    assert context.frame.notices == ["Reopen Ride — not yet implemented"]


# --------------------------------------- File ▸ Exit (no ride running)


def test_confirm_quit_confirmed_native_confirm_stamps_and_quits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-51/R-52: a confirmed native quit stamps closed_at."""
    store = _FakeStore()
    context = _context(store=store, presenter=_FakePresenter(_FakeEngine(RideStatus.FINISHED)))
    calls = _stub_show(monkeypatch, "show_confirm", wx.ID_OK)

    outcome = app_module._confirm_quit(context)

    parent, title, message, ok_label, cancel_label = calls[0][0]
    assert (parent, title, ok_label, cancel_label) == (
        context.frame,
        "Quit RiverCrossing?",
        "Quit",
        "Cancel",
    )
    assert "No ride is running" in message
    assert outcome is quit_flow.QuitOutcome.QUIT
    assert store.close_session_calls == 1


def test_confirm_quit_cancelled_native_confirm_stays_and_stamps_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel is the safe path: the app stays, nothing is stamped."""
    store = _FakeStore()
    context = _context(store=store, presenter=_FakePresenter(_FakeEngine(RideStatus.DRAFT)))
    _stub_show(monkeypatch, "show_confirm", wx.ID_CANCEL)

    outcome = app_module._confirm_quit(context)

    assert outcome is quit_flow.QuitOutcome.STAY
    assert store.close_session_calls == 0
