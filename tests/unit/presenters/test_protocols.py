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
from rivercrossing.standings import DEFAULT_TIEBREAK_ORDER, TieBreak
from rivercrossing.ui.presenters import (
    AppSettings,
    AuditPresenter,
    AuditRow,
    AuditView,
    CardVoid,
    ConsoleView,
    Counters,
    CrossingEdit,
    CsvPreview,
    Cue,
    DataSource,
    DetailPresenter,
    DetailView,
    DnfMark,
    EntryDetail,
    EntryLapRow,
    FeedRow,
    LibraryView,
    ManualDeal,
    ResultsPresenter,
    ResultsView,
    RiderMove,
    RiderRow,
    RidersView,
    RideSummary,
    SettingsView,
    SetupView,
    StandingsRow,
)

if TYPE_CHECKING:
    from rivercrossing.roster import EntryMode, PlateModel

from rivercrossing.htmlexport import ExportOptions

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

    # E4.4.1-E4.4.3: the Protocol grew the four members the live
    # presenter actually calls (the same "add the member once the
    # presenter calls it" precedent main_frame.py's own docstring
    # records for set_hide_times). Behavioral coverage lives in
    # tests/unit/presenters/test_console.py; these stay no-ops.
    def set_stop_enabled(self, *, enabled: bool) -> None:
        """Record the stop-button enablement (unused here)."""

    def set_hide_times(self, *, hide: bool) -> None:
        """Record the hide-times toggle (unused here)."""

    def show_clock(self, elapsed: str, remaining: str) -> None:
        """Record the clock labels (unused here)."""

    def set_entry_locked(self, *, locked: bool) -> None:
        """Record the entry-lock request (unused here)."""


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

    # E6.4.1: the Protocol grew the three members the live presenter
    # actually calls (the same "add the member once the presenter
    # calls it" precedent main_frame.py's own docstring records).
    # Behavior is covered in tests/unit/presenters/test_results.py;
    # these stay no-ops.
    def set_tiebreak_labels(self, labels: list[str]) -> None:
        """No-op fake."""

    def show_notice(self, text: str) -> None:
        """No-op fake."""

    def publish_options(self) -> ExportOptions:
        """No-op fake."""
        return ExportOptions()


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

    def selected_lap(self) -> EntryLapRow | None:
        """No-op fake: no lap selected."""
        return None

    def show_edit_crossing(
        self,
        *,
        adding: bool,  # noqa: ARG002 -- Protocol signature; the fake ignores values
        plate: str,  # noqa: ARG002 -- Protocol signature; the fake ignores values
        time: str,  # noqa: ARG002 -- Protocol signature; the fake ignores values
    ) -> CrossingEdit | None:
        """No-op fake: the dialog is cancelled."""
        return None

    def open_manual_deal(
        self,
        *,
        plate: str,  # noqa: ARG002 -- Protocol signature; the fake ignores the value
    ) -> ManualDeal | None:
        """No-op fake: the dialog is cancelled."""
        return None

    def open_void_card(
        self,
        *,
        card: str,  # noqa: ARG002 -- Protocol signature; the fake ignores values
        entry: str,  # noqa: ARG002 -- Protocol signature; the fake ignores values
    ) -> CardVoid | None:
        """No-op fake: the dialog is cancelled."""
        return None

    def open_dnf(
        self,
        *,
        entry: str,  # noqa: ARG002 -- Protocol signature; the fake ignores the value
    ) -> DnfMark | None:
        """No-op fake: the dialog is cancelled."""
        return None

    def open_move_rider(
        self,
        *,
        riders: tuple[str, ...],  # noqa: ARG002 -- Protocol signature; the fake ignores values
        teams: tuple[str, ...],  # noqa: ARG002 -- Protocol signature; the fake ignores values
    ) -> RiderMove | None:
        """No-op fake: the picker is cancelled."""
        return None

    def open_audit(self) -> None:
        """No-op fake."""

    def show_notice(self, text: str) -> None:
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

    def standings(
        self,
        order: tuple[TieBreak, ...] = DEFAULT_TIEBREAK_ORDER,  # noqa: ARG002 -- DataSource's signature; the fake ignores order
    ) -> list[StandingsRow]:
        """Return one fixed standings row for any order."""
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

    def results_stale(self, export_watermark: int | None) -> bool:  # noqa: ARG002 -- DataSource's signature; the fake is never stale
        """Return False: the fake publishes nothing that goes stale."""
        return False

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
        # ConsolePresenter is excluded: since E4.4.1 it takes
        # (view, engine, source) -- covered by its own dedicated
        # tests/unit/presenters/test_console.py suite.
        (ResultsPresenter, FakeResultsView()),
        (DetailPresenter, FakeDetailView()),
        (AuditPresenter, FakeAuditView()),
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
#
# ConsolePresenter behavior moved to test_console.py (E4.4.1):
# it holds (view, engine, source) and drives a real
# RideEngine -- every event handler (on_plate_entered/on_undo/
# on_arm_stop/on_stop_confirmed/on_start/on_hide_times/tick/on_finish)
# is covered there against a recording fake view and real engine
# fixtures. What remains here is Protocol conformance (FakeConsoleView
# above) and the wx-free import probe below.


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
