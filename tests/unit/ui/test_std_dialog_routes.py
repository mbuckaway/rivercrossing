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

E6.4.3 adds the finish route's second question: a BLOCKING evaluator
self-test failure asks one more danger confirm naming the failed
checks, and Cancel there finishes nothing. The route's gate is
``console.FINISH_GATE``, the same injectable module seam the console
presenter uses, so these tests answer it instead of running the real
suite.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING

import wx

from rivercrossing.hands import SelfTestCheck, SelfTestReport
from rivercrossing.ride import RideStatus
from rivercrossing.ui import app as app_module
from rivercrossing.ui import quit_flow, std_dialogs
from rivercrossing.ui.presenters import console as console_module
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
        # E6.4.3: the gate report the finish route hands on_finish.
        self.reports: list[SelfTestReport | None] = []

    def on_finish(self, report: SelfTestReport | None = None) -> None:
        """Record the finish action and the report it was given."""
        self.actions.append("finish")
        self.reports.append(report)

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


def _stub_show_sequence(
    monkeypatch: pytest.MonkeyPatch, name: str, results: list[int]
) -> list[tuple[tuple[object, ...], dict[str, object]]]:
    """Swap ``std_dialogs.<name>`` for a recorder using *results*."""
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    answers = iter(results)

    def _show(*args: object, **kwargs: object) -> int:
        calls.append((args, kwargs))
        return next(answers)

    monkeypatch.setattr(std_dialogs, name, _show)
    return calls


def _report(*checks: SelfTestCheck) -> SelfTestReport:
    """Build a self-test report over *checks*."""
    return SelfTestReport(checks=checks)


def _checked(name: str, *, passed: bool, blocking: bool = True) -> SelfTestCheck:
    """Build one self-test check with a zero duration and no detail."""
    return SelfTestCheck(
        name=name, passed=passed, duration_seconds=0.0, detail="", blocking=blocking
    )


def _stub_gate(monkeypatch: pytest.MonkeyPatch, report: SelfTestReport) -> None:
    """Answer the E6.4.3 finish gate with *report*, never the suite.

    ``console.FINISH_GATE`` is the documented injectable seam (the same
    shape as ``settings.WARN``), so swapping it is the module-seam use
    the production code expects -- not a mock of an internal module.
    """
    monkeypatch.setattr(console_module, "FINISH_GATE", lambda: report)


# ---------------------------------------------------- Finish Ride…


def test_handle_finish_route_confirmed_danger_finishes_the_ride(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confirmed Finish fires the live presenter's on_finish."""
    presenter = _FakePresenter(_FakeEngine(RideStatus.RUNNING))
    context = _context(presenter=presenter)
    calls = _stub_show(monkeypatch, "show_danger", wx.ID_OK)
    report = _report(_checked("7,462 distinct ranks", passed=True))
    _stub_gate(monkeypatch, report)

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
    assert presenter.reports == [report]


def test_handle_finish_route_given_a_blocking_failure_asks_the_override_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E6.4.3: a red self-test asks a second, check-naming confirm.

    The operator is never stranded at the end of a multi-hour ride: the
    override question names every failed check and finishes the ride on
    OK, handing the report on so the presenter records the override.
    """
    presenter = _FakePresenter(_FakeEngine(RideStatus.RUNNING))
    context = _context(presenter=presenter)
    calls = _stub_show(monkeypatch, "show_danger", wx.ID_OK)
    report = _report(
        _checked("7,462 distinct ranks", passed=False),
        _checked("compare() total order", passed=False),
    )
    _stub_gate(monkeypatch, report)

    app_module._handle_finish_route(context)

    parent, title, message, ok_label, cancel_label = calls[1][0]
    assert (parent, title, ok_label, cancel_label) == (
        context.frame,
        "Evaluator Self-Test Failed",
        "Finish anyway",
        "Cancel",
    )
    assert "7,462 distinct ranks" in message
    assert "compare() total order" in message
    assert presenter.actions == ["finish"]
    assert presenter.reports == [report]


def test_handle_finish_route_given_a_clean_gate_never_asks_the_override_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A green gate shows the finish confirm alone, not a second one."""
    presenter = _FakePresenter(_FakeEngine(RideStatus.RUNNING))
    context = _context(presenter=presenter)
    calls = _stub_show(monkeypatch, "show_danger", wx.ID_OK)
    _stub_gate(
        monkeypatch,
        _report(
            _checked("7,462 distinct ranks", passed=True),
            _checked("Whole-field 180×12 timing", passed=False, blocking=False),  # noqa: RUF001
        ),
    )

    app_module._handle_finish_route(context)

    assert [call[0][1] for call in calls] == ["Finish Ride?"]
    assert presenter.actions == ["finish"]


def test_handle_finish_route_given_a_cancelled_override_does_not_finish(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E6.4.3: declining the override does not finish the ride."""
    presenter = _FakePresenter(_FakeEngine(RideStatus.RUNNING))
    context = _context(presenter=presenter)
    calls = _stub_show_sequence(monkeypatch, "show_danger", [wx.ID_OK, wx.ID_CANCEL])
    _stub_gate(monkeypatch, _report(_checked("compare() total order", passed=False)))

    app_module._handle_finish_route(context)

    assert len(calls) == 2
    assert presenter.actions == []
    assert presenter.reports == []


def test_handle_finish_route_reopened_ride_offers_finish_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """E7.2.2: a REOPENED ride is finished again, labelled properly."""
    presenter = _FakePresenter(_FakeEngine(RideStatus.REOPENED))
    context = _context(presenter=presenter)
    calls = _stub_show(monkeypatch, "show_danger", wx.ID_OK)
    _stub_gate(monkeypatch, _report(_checked("Joker vector table (28)", passed=True)))

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


def test_handle_finish_route_cancelled_danger_never_runs_the_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancelled finish confirm does no evaluator work at all.

    Both the observable state (nothing was finished) and the seam
    record (the suite never ran) are asserted, so this is never a
    call-record-only test.
    """
    presenter = _FakePresenter(_FakeEngine(RideStatus.RUNNING))
    context = _context(presenter=presenter)
    _stub_show(monkeypatch, "show_danger", wx.ID_CANCEL)
    gate_reports: list[int] = []

    def _record_gate() -> SelfTestReport:
        """Record one gate consultation; never expected to run."""
        gate_reports.append(1)
        return _report(_checked("7,462 distinct ranks", passed=True))

    monkeypatch.setattr(console_module, "FINISH_GATE", _record_gate)

    app_module._handle_finish_route(context)

    assert (presenter.actions, gate_reports) == ([], [])


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
