# SPDX-License-Identifier: GPL-3.0-only
"""Presenter protocol tests (E1.2.3) -- tests first, per R-70.

Every window gets a view-interface so its presenter is testable
without wx (module-skeletons.md ui.presenters; R-71). This suite
proves, for each of the eight ``*View`` Protocols and the shared
``DataSource`` Protocol:

1. A ``FakeView``/``FakeDataSource`` implementing the full member set
   satisfies the Protocol via ``isinstance`` (``@runtime_checkable``).
2. Each presenter accepts ``(view, data_source)`` and holds both.
3. A view missing one member fails ``mypy --strict`` by name -- a
   real subprocess mypy run against a fixture file, never a runtime
   ``hasattr`` stand-in.
4. No presenter module ever imports ``wx``, proven in a subprocess
   this test does not itself pollute.
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from rivercrossing.ride import RideStatus
from rivercrossing.ui.presenters import (
    AppSettings,
    AuditPresenter,
    AuditRow,
    AuditView,
    ConsolePresenter,
    ConsoleView,
    Counters,
    CsvPreview,
    Cue,
    DataSource,
    DetailPresenter,
    DetailView,
    EntryDetail,
    EntryLapRow,
    FeedRow,
    LibraryPresenter,
    LibraryView,
    ResultsPresenter,
    ResultsView,
    RiderRow,
    RidersView,
    RideSummary,
    SettingsPresenter,
    SettingsView,
    SetupView,
    StandingsRow,
)

if TYPE_CHECKING:
    from rivercrossing.htmlexport import ExportOptions
    from rivercrossing.roster import EntryMode, PlateModel

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"

_PRESENTER_MODULES = (
    "rivercrossing.ui.presenters.audit",
    "rivercrossing.ui.presenters.console",
    "rivercrossing.ui.presenters.data_source",
    "rivercrossing.ui.presenters.detail",
    "rivercrossing.ui.presenters.library",
    "rivercrossing.ui.presenters.results",
    "rivercrossing.ui.presenters.riders",
    "rivercrossing.ui.presenters.selftest",
    "rivercrossing.ui.presenters.settings",
    "rivercrossing.ui.presenters.setup",
)


# ----------------------------------------------------------- fake views


class FakeConsoleView:
    """A complete ``ConsoleView`` implementation for headless tests."""

    def show_feed(self, rows: list[FeedRow]) -> None:
        """Record the fed rows for later assertion (unused here)."""

    def show_counters(self, c: Counters) -> None:
        """Record the counters (unused here)."""

    def flash_crossing(self, r: FeedRow) -> None:
        """Record the flashed crossing (unused here)."""

    def set_state(self, status: RideStatus) -> None:
        """Record the ride state (unused here)."""

    def focus_entry(self) -> None:
        """Record the focus request (unused here)."""

    def play(self, cue: Cue) -> None:
        """Record the played cue (unused here)."""

    def show_notice(self, text: str) -> None:
        """Record the shown notice (unused here)."""

    def clear_entry(self) -> None:
        """Record the clear request (unused here)."""


class FakeSetupView:
    """A complete ``SetupView`` implementation for headless tests."""

    def set_team_fields_enabled(self, *, enabled: bool) -> None:
        """No-op fake."""

    def set_entry_locked(self, *, locked: bool) -> None:
        """No-op fake."""

    def show_deck_count(self, count: int) -> None:
        """No-op fake."""

    def show_entry_settings(
        self, *, entry_mode: EntryMode, max_team_size: int, plate_model: PlateModel
    ) -> None:
        """No-op fake."""

    def show_validation(self, message: str) -> None:
        """No-op fake."""


class FakeRidersView:
    """A complete ``RidersView`` implementation for headless tests."""

    def show_riders(self, rows: list[RiderRow]) -> None:
        """No-op fake."""

    def show_team_choices(self, names: list[str]) -> None:
        """No-op fake."""

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """No-op fake."""

    def show_csv_preview(self, preview: CsvPreview) -> None:
        """No-op fake."""

    def set_import_enabled(self, *, enabled: bool) -> None:
        """No-op fake."""

    def show_form(self, *, plate: str, name: str, team: str) -> None:
        """No-op fake."""

    def set_team_ui_visible(self, *, visible: bool) -> None:
        """No-op fake."""

    def show_validation(self, message: str) -> None:
        """No-op fake."""

    def prompt_new_team_name(self) -> str | None:
        """No-op fake."""
        return None


class FakeResultsView:
    """A complete ``ResultsView`` implementation for headless tests."""

    def show_standings(self, rows: list[StandingsRow]) -> None:
        """No-op fake."""

    def set_stale(self, *, stale: bool) -> None:
        """No-op fake."""

    def show_publish_options(self, options: ExportOptions) -> None:
        """No-op fake."""


class FakeLibraryView:
    """A complete ``LibraryView`` implementation for headless tests."""

    def show_rides(self, rows: list[RideSummary]) -> None:
        """No-op fake."""

    def set_delete_enabled(self, *, enabled: bool) -> None:
        """No-op fake."""


class FakeDetailView:
    """A complete ``DetailView`` implementation for headless tests."""

    def show_entry(self, detail: EntryDetail) -> None:
        """No-op fake."""

    def set_move_rider_enabled(self, *, enabled: bool) -> None:
        """No-op fake."""

    def show_edit_crossing(self, *, adding: bool, plate: str, time: str) -> None:
        """No-op fake."""


class FakeAuditView:
    """A complete ``AuditView`` implementation for headless tests."""

    def show_audit_rows(self, rows: list[AuditRow]) -> None:
        """No-op fake."""

    def set_entry_filter(self, entry: str) -> None:
        """No-op fake."""


class FakeSettingsView:
    """A complete ``SettingsView`` implementation for headless tests."""

    def show_settings(self, settings: AppSettings) -> None:
        """No-op fake."""


class FakeDataSource:
    """A complete ``DataSource`` implementation for headless tests."""

    def feed_rows(self) -> list[FeedRow]:
        """Return one fixed feed row."""
        return [
            FeedRow(
                time="14:22:41",
                plate="123",
                entry="Sam Ellis",
                lap=4,
                lap_time="22:41",
                total="1:31:04",
                card="9H",
            )
        ]

    def counters(self) -> Counters:
        """Return one fixed counter set."""
        return Counters(
            crossings=1124, cards_dealt=1092, on_course=42, shoe_remaining=41, shoe_total=108
        )

    def rides(self) -> list[RideSummary]:
        """Return one fixed ride library row."""
        return [
            RideSummary(
                name="GORBA EPIC 2026", date="2026-09-20", status=RideStatus.RUNNING, entries=180
            )
        ]

    def riders(self) -> list[RiderRow]:
        """Return one fixed rider row."""
        return [RiderRow(plate="77", name="A. Roy", team="Trail Blazers")]

    def entry_detail(self, plate: str) -> EntryDetail:
        """Return one fixed entry detail for any plate."""
        return EntryDetail(
            header=f"Team · {plate}",
            members="A. Roy (77)",
            cards_held=("9H",),
            laps=(EntryLapRow(lap=9, time="14:22:18", lap_time="19:55", rider="78", card="KC"),),
        )

    def standings(self) -> list[StandingsRow]:
        """Return one fixed standings row."""
        return [
            StandingsRow(
                place=1,
                plate="77",
                entry="Trail Blazers",
                laps=9,
                total="5:44:02",
                best5=("KS", "KC", "KD", "JK", "9H"),
                hand="Four of a kind, kings",
            )
        ]

    def audit_rows(self) -> list[AuditRow]:
        """Return one fixed audit row."""
        return [
            AuditRow(
                when="14:23:02", who="scorer", action="Void crossing", entry="45", reason="mis-key"
            )
        ]

    def ride_status(self) -> RideStatus:
        """Return one fixed ride status."""
        return RideStatus.RUNNING


# ------------------------------------------------ protocol conformance


@pytest.mark.parametrize(
    ("fake", "protocol"),
    [
        (FakeConsoleView(), ConsoleView),
        (FakeSetupView(), SetupView),
        (FakeRidersView(), RidersView),
        (FakeResultsView(), ResultsView),
        (FakeLibraryView(), LibraryView),
        (FakeDetailView(), DetailView),
        (FakeAuditView(), AuditView),
        (FakeSettingsView(), SettingsView),
        (FakeDataSource(), DataSource),
    ],
)
def test_fake_implementation_satisfies_its_protocol(fake: object, protocol: type) -> None:
    """Each complete fake structurally satisfies its Protocol."""
    assert isinstance(fake, protocol)


# ------------------------------------------------ presenter/DataSource


@pytest.mark.parametrize(
    ("presenter_cls", "view"),
    [
        (ConsolePresenter, FakeConsoleView()),
        (ResultsPresenter, FakeResultsView()),
        (LibraryPresenter, FakeLibraryView()),
        (DetailPresenter, FakeDetailView()),
        (AuditPresenter, FakeAuditView()),
        (SettingsPresenter, FakeSettingsView()),
    ],
)
def test_presenter_holds_the_view_and_data_source_it_was_given(
    presenter_cls: type, view: object
) -> None:
    """Every presenter stores the exact view and data source given.

    ``RidersPresenter`` and ``SetupPresenter`` are excluded: since
    E3.2.1/E3.2.2 (riders) and E3.5.1 (setup) they take ``(view,
    roster)`` instead -- each covered by its own dedicated suite,
    ``tests/unit/presenters/test_riders.py``/``test_setup.py``.
    """
    data_source = FakeDataSource()

    presenter = presenter_cls(view, data_source)

    assert presenter.view is view
    assert presenter.data_source is data_source


# --------------------------------------------------- ConsolePresenter


@pytest.mark.parametrize(
    ("method_name", "args", "kwargs"),
    [
        ("on_undo", (), {}),
        ("on_arm_stop", (), {"armed": True}),
        ("on_arm_stop", (), {"armed": False}),
        ("on_stop_confirmed", (), {}),
        ("on_hide_times", (), {"hide": True}),
        ("on_hide_times", (), {"hide": False}),
        ("tick", (), {}),
    ],
)
def test_console_presenter_method_is_a_no_op_returning_none(
    method_name: str, args: tuple[object, ...], kwargs: dict[str, object]
) -> None:
    """Every named ConsolePresenter method exists and no-ops."""
    presenter = ConsolePresenter(FakeConsoleView(), FakeDataSource())
    method = getattr(presenter, method_name)

    result = method(*args, **kwargs)

    assert result is None


class RecordingConsoleView:
    """A ``ConsoleView`` spy recording each call, in order (D1 wiring).

    Distinct from :class:`FakeConsoleView`: the ``on_plate_entered``
    cases below assert call *order* and *argument content*, which a
    no-op fake cannot record.
    """

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, tuple[object, ...]]] = []

    def show_feed(self, rows: list[FeedRow]) -> None:
        """Record the fed rows."""
        self.calls.append(("show_feed", (rows,)))

    def show_counters(self, c: Counters) -> None:
        """Record the counters."""
        self.calls.append(("show_counters", (c,)))

    def flash_crossing(self, r: FeedRow) -> None:
        """Record the flashed crossing."""
        self.calls.append(("flash_crossing", (r,)))

    def set_state(self, status: RideStatus) -> None:
        """Record the ride state."""
        self.calls.append(("set_state", (status,)))

    def focus_entry(self) -> None:
        """Record the focus request."""
        self.calls.append(("focus_entry", ()))

    def play(self, cue: Cue) -> None:
        """Record the played cue."""
        self.calls.append(("play", (cue,)))

    def show_notice(self, text: str) -> None:
        """Record the shown notice."""
        self.calls.append(("show_notice", (text,)))

    def clear_entry(self) -> None:
        """Record the clear request."""
        self.calls.append(("clear_entry", ()))


# --- on_plate_entered: D1 placeholder behaviour (A3, A5) --------------


def test_on_plate_entered_given_text_shows_notice_clears_and_refocuses() -> None:
    """A5: notice, then clear, then refocus -- in that exact order."""
    view = RecordingConsoleView()
    presenter = ConsolePresenter(view, FakeDataSource())

    presenter.on_plate_entered("123")

    assert view.calls == [
        ("show_notice", ("Plate 123 — recording engine lands in EPIC 4",)),
        ("clear_entry", ()),
        ("focus_entry", ()),
    ]


def test_on_plate_entered_strips_surrounding_whitespace_into_the_notice() -> None:
    """Leading/trailing whitespace never reaches the notice text."""
    view = RecordingConsoleView()
    presenter = ConsolePresenter(view, FakeDataSource())

    presenter.on_plate_entered("  123  ")

    assert view.calls[0] == ("show_notice", ("Plate 123 — recording engine lands in EPIC 4",))


@pytest.mark.parametrize("text", ["", "   "], ids=["empty", "whitespace_only"])
def test_on_plate_entered_given_blank_text_only_refocuses(text: str) -> None:
    """A3: a blank (or whitespace-only) plate only refocuses."""
    view = RecordingConsoleView()
    presenter = ConsolePresenter(view, FakeDataSource())

    presenter.on_plate_entered(text)

    assert view.calls == [("focus_entry", ())]


# -------------------------------------------------------- mypy negative


def test_console_view_missing_method_fails_mypy_typecheck() -> None:
    """A ConsoleView fake missing `play` fails mypy --strict, naming it.

    Honest static-typing test, not a runtime hasattr stand-in: this
    spawns the real ``mypy`` CLI against a fixture file that omits
    ``play`` from an otherwise-complete ``ConsoleView`` implementation
    and asserts the failure names the missing member.
    """
    fixture = _FIXTURES_DIR / "incomplete_console_view.py"

    # --no-color-output: CI exports FORCE_COLOR=1 and mypy honours it,
    # wrapping the quoted names below in ANSI codes on win32 (measured
    # on windows-latest) -- the substring asserts need plain text.
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "mypy", "--no-color-output", str(fixture)],
        capture_output=True,
        text=True,
        cwd=_REPO_ROOT,
        check=False,
    )

    assert result.returncode == 1
    assert 'is missing following "ConsoleView" protocol member' in result.stdout
    assert re.search(r"note:\s+play\b", result.stdout)


# --------------------------------------------------------------- no wx

_PRESENTER_IMPORT_PROBE = """
import sys

import {module}

assert "wx" not in sys.modules, "wx leaked into {module}"
"""


@pytest.mark.parametrize("module_name", _PRESENTER_MODULES)
def test_presenter_module_import_does_not_load_wx(module_name: str) -> None:
    """Executing any presenters module never pulls in wx.

    Runs in a fresh subprocess interpreter this test does not itself
    pollute. ``rivercrossing.ui``'s guard is lazy (see its
    docstring): importing the package no longer imports wx, so
    reaching a presenter submodule through its parent package no
    longer drags wx in either -- no stub of ``rivercrossing.ui`` is
    needed to make this probe honest.
    """
    probe = _PRESENTER_IMPORT_PROBE.format(module=module_name)
    result = subprocess.run(  # noqa: S603
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
