# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for Phase 11 H1/H2's dialog seam and copy.

``views.dialogs.run_dialog`` is the one seam every XRC dialog shows
through. It centres the loaded dialog over the opener's own top-level
window -- the screen when there is none -- before ``ShowModal``, so
every modal lands over the console rather than wherever the platform
put it, and it never re-parents: a ``wx.Dialog`` must stay a
top-level window, and re-parenting it to the frame renders its
controls inside the frame on Cocoa (the measured H1 regression this
suite pins). Real ``wx.Dialog`` windows need a desktop (and would
block on ``ShowModal``), so the tests drive a recording fake and
stub only the two wx-touching collaborators the seam also calls --
``wire_close_button`` and the light-mode panel tint -- exactly the
way ``test_std_dialogs.py`` swaps ``wx.MessageDialog``.

The second half pins the copy H2 moved out of the four retired XRC
dialogs, so the ride-naming sentences those windows carried cannot
go blank unnoticed (UX-DESKTOP §4: a confirm names its object).

Phase 6 adds the two dialogs' own default size. XRC has no
window-level minsize, so each view's ``_apply_min_size`` fits the
built dialog and then floors *and* opens it at its own scale pair:
Phase 3 narrowed ``csv_preview_dlg`` to 2x the fitted width and 2x
the height, while ``rider_issues_dlg`` keeps 3x/2x. A real
``wx.Dialog`` needs a desktop, so these tests drive the same kind of
recording window double the seam tests above use and call the sizing
step directly -- the constructor next to it binds every control the
.xrc window carries.
"""

import json
import string
from typing import TYPE_CHECKING

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.logging import VERBOSE_LOG_NAME, VerboseLog
from rivercrossing.ui.views import dialogs
from rivercrossing.ui.views.rider_editor import CsvPreviewDialog, RiderEditor
from rivercrossing.ui.views.rider_issues import RiderIssuesView

if TYPE_CHECKING:
    from pathlib import Path

_SCRIPTED_MODAL_RESULT = 40001  # a stand-in modal id, no real wx stock id
_DEFAULT_WINDOW_RECT = (100, 50, 800, 600)  # x, y, width, height


class _FakeDialog:
    """Record the positioning calls; report a scripted modal result."""

    def __init__(
        self,
        modal_result: int = _SCRIPTED_MODAL_RESULT,
        name: str = "settings_dlg",
        size: tuple[int, int] = (200, 100),
    ) -> None:
        """Start empty; report *modal_result* against *size*."""
        self.modal_result = modal_result
        self.name = name
        self.size = wx.Size(*size)
        self.reparent_calls = 0
        self.centre_on_parent_calls = 0
        self.show_modal_calls = 0
        self.set_position_calls: list[tuple[int, int]] = []

    def GetName(self) -> str:  # noqa: N802 -- wx API name the SUT calls
        """Report the dialog's frozen XRC name."""
        return self.name

    def GetSize(self) -> wx.Size:  # noqa: N802 -- wx API name the SUT calls
        """Report the scripted dialog size the seam centres with."""
        return self.size

    def SetPosition(self, position: wx.Point) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the seam's computed centre-over-window position."""
        self.set_position_calls.append((position.x, position.y))

    def Reparent(self, _parent: object) -> None:  # noqa: N802 -- wx API name the SUT must not call
        """Record a re-parent attempt; the seam must never make one."""
        self.reparent_calls += 1

    def CentreOnParent(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one centring call."""
        self.centre_on_parent_calls += 1

    def ShowModal(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Record the show and report the scripted result."""
        self.show_modal_calls += 1
        return self.modal_result


class _FakeTopLevel:
    """A top-level window double with a scripted screen rect."""

    def __init__(self, rect: tuple[int, int, int, int] = _DEFAULT_WINDOW_RECT) -> None:
        """Return *rect* (x, y, width, height) from GetScreenRect."""
        self._rect = wx.Rect(*rect)

    def GetScreenRect(self) -> wx.Rect:  # noqa: N802 -- wx API name the SUT calls
        """Report the scripted top-level window's screen rectangle."""
        return self._rect


class _FakeOpener:
    """A window double answering name, focus and top-level calls."""

    def __init__(self, top_level: object | None = None, name: str = "mi_settings") -> None:
        """Return *top_level*, or ``None`` when there is none."""
        self._top_level = top_level
        self.name = name
        self.focus_calls = 0

    def GetName(self) -> str:  # noqa: N802 -- wx API name the SUT calls
        """Report the opener's frozen name."""
        return self.name

    def GetTopLevelParent(self) -> object | None:  # noqa: N802 -- wx API name the SUT calls
        """Report the scripted top-level window, or ``None``."""
        return self._top_level

    def SetFocus(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record one focus restore."""
        self.focus_calls += 1


@pytest.fixture
def tinted_dialogs(monkeypatch: pytest.MonkeyPatch) -> list[object]:
    """Stub the seam's two wx-touching collaborators; record the tint.

    ``wire_close_button`` walks a real dialog's children, the theme
    tint reads the live system appearance, and the verbose log reads
    the live ``wx.App``, so all three are swapped for recorders: they
    are the GUI I/O boundary of this seam (T-10), not the logic under
    test. A test that wants the log drives its own replacement (see
    :func:`test_run_dialog_given_a_verbose_log_records_the_dialog_open`).
    """
    tinted: list[object] = []
    monkeypatch.setattr(dialogs, "wire_close_button", lambda _dialog: None)
    monkeypatch.setattr(dialogs.theme, "apply_light_mode_panel_bg", tinted.append)
    monkeypatch.setattr(dialogs, "_active_verbose_log", lambda: None)
    return tinted


def test_run_dialog_never_reparents_the_dialog(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture only stubs wx
) -> None:
    """A wx.Dialog stays top-level; the seam must not Reparent."""
    dialog = _FakeDialog()

    dialogs.run_dialog(dialog, _FakeOpener())

    assert dialog.reparent_calls == 0


@pytest.mark.parametrize(
    "case",
    [
        ((100, 50, 800, 600), (200, 100), (400, 300)),
        ((0, 0, 801, 601), (200, 100), (300, 250)),
    ],
    ids=["even-deltas", "odd-deltas"],
)
def test_run_dialog_centres_the_dialog_over_the_openers_top_level_window(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture only stubs wx
    case: tuple[tuple[int, int, int, int], tuple[int, int], tuple[int, int]],
) -> None:
    """The dialog's top-left lands at the window's centre."""
    rect, size, expected_position = case
    dialog = _FakeDialog(size=size)

    dialogs.run_dialog(dialog, _FakeOpener(_FakeTopLevel(rect)))

    assert (dialog.set_position_calls, dialog.centre_on_parent_calls) == (
        [expected_position],
        0,
    )


def test_run_dialog_without_a_top_level_parent_falls_back_to_screen_centring(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture only stubs wx
) -> None:
    """A parentless opener keeps the screen-centring fallback."""
    dialog = _FakeDialog()

    dialogs.run_dialog(dialog, _FakeOpener())

    assert (dialog.centre_on_parent_calls, dialog.set_position_calls) == (1, [])


def test_run_dialog_returns_the_modal_result(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture only stubs wx
) -> None:
    """The caller sees ShowModal's id, unchanged."""
    dialog = _FakeDialog(modal_result=-7)

    result = dialogs.run_dialog(dialog, _FakeOpener())

    assert result == -7


def test_run_dialog_restores_focus_to_the_opener_after_the_modal(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture only stubs wx
) -> None:
    """spec.md §13's last dialog rule still runs."""
    opener = _FakeOpener()

    dialogs.run_dialog(_FakeDialog(), opener)

    assert opener.focus_calls == 1


def test_run_dialog_applies_the_light_mode_tint_before_showing(
    tinted_dialogs: list[object],
) -> None:
    """The ux-polish tint is applied once, before ShowModal."""
    dialog = _FakeDialog()

    dialogs.run_dialog(dialog, _FakeOpener())

    assert tinted_dialogs == [dialog]


# --- F4: the verbose log's dialog record -----------------------------


def test_run_dialog_given_a_verbose_log_records_the_dialog_open(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    tinted_dialogs: list[object],  # noqa: ARG001
) -> None:
    """F4: the one dialog seam records the dialog's name and opener."""
    log = VerboseLog(tmp_path / VERBOSE_LOG_NAME)
    monkeypatch.setattr(dialogs, "_active_verbose_log", lambda: log)

    dialogs.run_dialog(_FakeDialog(name="settings_dlg"), _FakeOpener(name="mi_settings"))

    records = [
        json.loads(line)
        for line in (tmp_path / VERBOSE_LOG_NAME).read_text(encoding="utf-8").splitlines()
        if line
    ]
    assert [record["msg"] for record in records] == ["dialog settings_dlg (from mi_settings)"]


def test_run_dialog_without_a_verbose_log_still_shows_the_dialog(
    tinted_dialogs: list[object],  # noqa: ARG001 -- the seam fixture stubs the log to None
) -> None:
    """F4: an app with no log shows the dialog unchanged."""
    dialog = _FakeDialog()

    result = dialogs.run_dialog(dialog, _FakeOpener())

    assert (result, dialog.show_modal_calls) == (_SCRIPTED_MODAL_RESULT, 1)


# --- H2: the copy the four retired XRC dialogs carried ----------------


def test_finish_ride_message_states_the_lock_and_the_reopen_offer() -> None:
    """The finish confirm explains what finishing does."""
    message = dialogs.finish_ride_message()

    assert "evaluator self-test" in message
    assert "reopen" in message.lower()


def test_duplicate_ride_message_names_the_ride_and_the_copied_parts() -> None:
    """Duplicate names the ride and promises setup + roster only."""
    message = dialogs.duplicate_ride_message("GORBA EPIC 2026")

    assert 'Duplicate "GORBA EPIC 2026" as a new DRAFT ride?' in message
    assert "no timing data" in message


def test_reopen_ride_message_names_the_ride_and_the_recompute() -> None:
    """Reopen names the ride and what the corrections state costs."""
    message = dialogs.reopen_ride_message("Club poker night")

    assert 'Reopen "Club poker night" for corrections?' in message
    assert "recompute" in message


_RIDE_NAME_STRATEGY = st.text(
    alphabet=string.ascii_letters + string.digits + " -",
    min_size=1,
    max_size=40,
).filter(lambda name: name.strip() == name)


@given(ride_name=_RIDE_NAME_STRATEGY)
def test_ride_confirm_messages_given_any_ride_name_embed_it_verbatim(
    ride_name: str,
) -> None:
    """T-7 property: each confirm quotes the ride name exactly once."""
    duplicate = dialogs.duplicate_ride_message(ride_name)
    reopen = dialogs.reopen_ride_message(ride_name)

    assert duplicate.count(f'"{ride_name}"') == 1
    assert reopen.count(f'"{ride_name}"') == 1


# --- Phase 6: the two dialogs' 3x-wide / 2x-tall default size ---------


class _SizingDialog:
    """A dialog double recording every sizing call the SUT makes."""

    def __init__(self, fitted: tuple[int, int] = (400, 300)) -> None:
        """Report *fitted* from GetSize, like a just-Fit() dialog."""
        self._fitted = wx.Size(*fitted)
        self.calls: list[str] = []
        self.min_size: wx.Size | None = None
        self.size: wx.Size | None = None

    def Fit(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the fitting call."""
        self.calls.append("Fit")

    def GetSize(self) -> wx.Size:  # noqa: N802 -- wx API name the SUT calls
        """Record the read and report the fitted size."""
        self.calls.append("GetSize")
        return wx.Size(self._fitted)

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the floor the SUT applied."""
        self.calls.append("SetMinSize")
        self.min_size = size

    def SetSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the size the SUT opened the dialog at."""
        self.calls.append("SetSize")
        self.size = size


# The two sizing dialogs, each with its own (width, height) scale
# pair. Phase 3 narrows csv_preview_dlg from 3x to 2x; rider_issues_dlg
# keeps Phase 6's 3x/2x.
_DIALOG_VIEWS: list[tuple[type[CsvPreviewDialog | RiderIssuesView], int, int]] = [
    (CsvPreviewDialog, 2, 2),
    (RiderIssuesView, 3, 2),
]
_DIALOG_VIEW_IDS = ["csv_preview_dlg", "rider_issues_dlg"]


def _view_over(
    view_class: type[CsvPreviewDialog | RiderEditor | RiderIssuesView],
    dialog: _SizingDialog,
) -> CsvPreviewDialog | RiderEditor | RiderIssuesView:
    """Return *view_class* over *dialog*, no .xrc window loaded.

    ``__init__`` binds every control the .xrc window carries, which
    needs a desktop; the sizing step beside it reads only
    ``self.dialog``, so the instance is made without it -- the window
    itself is the same kind of stand-in the seam tests above use.
    """
    view = object.__new__(view_class)
    view.dialog = dialog
    return view


@pytest.mark.parametrize(
    ("view_class", "width_scale", "height_scale"), _DIALOG_VIEWS, ids=_DIALOG_VIEW_IDS
)
def test_dialog_view_apply_min_size_given_a_fitted_size_floors_it_at_its_own_scale(
    view_class: type[CsvPreviewDialog | RiderIssuesView],
    width_scale: int,
    height_scale: int,
) -> None:
    """Each dialog floors itself at its own (width, height) scale."""
    dialog = _SizingDialog(fitted=(400, 300))

    _view_over(view_class, dialog)._apply_min_size()

    expected = (400 * width_scale, 300 * height_scale)
    assert (dialog.min_size.width, dialog.min_size.height) == expected


@pytest.mark.parametrize(
    ("view_class", "width_scale", "height_scale"), _DIALOG_VIEWS, ids=_DIALOG_VIEW_IDS
)
def test_dialog_view_apply_min_size_given_a_fitted_size_opens_it_at_its_own_scale(
    view_class: type[CsvPreviewDialog | RiderIssuesView],
    width_scale: int,
    height_scale: int,
) -> None:
    """The dialog opens at its own floor, not merely bounded by it."""
    dialog = _SizingDialog(fitted=(400, 300))

    _view_over(view_class, dialog)._apply_min_size()

    expected = (400 * width_scale, 300 * height_scale)
    assert (dialog.size.width, dialog.size.height) == expected


@pytest.mark.parametrize(
    ("view_class", "width_scale", "height_scale"), _DIALOG_VIEWS, ids=_DIALOG_VIEW_IDS
)
def test_dialog_view_apply_min_size_measures_the_fitted_size_before_scaling_by_its_own_scale(
    view_class: type[CsvPreviewDialog | RiderIssuesView],
    width_scale: int,
    height_scale: int,
) -> None:
    """Scale what Fit() just measured, never a stale size."""
    dialog = _SizingDialog(fitted=(400, 300))

    _view_over(view_class, dialog)._apply_min_size()

    expected = (400 * width_scale, 300 * height_scale)
    assert (dialog.calls, (dialog.min_size.width, dialog.min_size.height)) == (
        ["Fit", "GetSize", "SetMinSize", "SetSize"],
        expected,
    )


_CSV_SCALE_CASES = [
    ((0, 0), (0, 0)),  # T-4 boundary: below any real fitted size
    ((1, 1), (2, 2)),  # T-4 boundary: min
    ((2, 3), (4, 6)),  # T-4 boundary: min + 1
    ((640, 320), (1280, 640)),  # a realistic fitted dialog
]


@pytest.mark.parametrize(("fitted", "expected"), _CSV_SCALE_CASES)
def test_csv_preview_dialog_apply_min_size_given_boundary_fitted_sizes_scales_by_two_and_two(
    fitted: tuple[int, int],
    expected: tuple[int, int],
) -> None:
    """Phase 3: every fitted size scales by exactly 2 and 2 (T-4)."""
    dialog = _SizingDialog(fitted=fitted)

    _view_over(CsvPreviewDialog, dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == expected


@given(
    case=st.sampled_from(_DIALOG_VIEWS),
    width=st.integers(min_value=0, max_value=10_000),
    height=st.integers(min_value=0, max_value=10_000),
)
def test_dialog_view_apply_min_size_given_any_fitted_size_scales_by_its_own_pair(
    case: tuple[type[CsvPreviewDialog | RiderIssuesView], int, int],
    width: int,
    height: int,
) -> None:
    """T-7 property: the floor is the view's own scale pair."""
    view_class, width_scale, height_scale = case
    dialog = _SizingDialog(fitted=(width, height))

    _view_over(view_class, dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == (
        width * width_scale,
        height * height_scale,
    )


def test_rider_editor_apply_min_size_given_the_w7_canvas_keeps_its_own_floor() -> None:
    """Phase 6 leaves the editor's own 1280x560 floor alone."""
    dialog = _SizingDialog(fitted=(10, 10))

    _view_over(RiderEditor, dialog)._apply_min_size()

    assert (dialog.min_size.width, dialog.min_size.height) == (1280, 560)


# --- Phase D: RiderIssuesView's own selection reconcile --------------


class _FakeSelection:
    """A ``wx.dataview.DataViewItem`` double answering ``IsOk``."""

    def __init__(self, *, ok: bool) -> None:
        """Report *ok* from IsOk."""
        self._ok = ok

    def IsOk(self) -> bool:  # noqa: N802 -- wx API name the SUT calls
        """Report whether this is a real selection."""
        return self._ok


class _FakeIssuesList:
    """An ``issues_list`` double returning one scripted selection."""

    def __init__(self, selection: _FakeSelection) -> None:
        """Return *selection* from every GetSelection call."""
        self._selection = selection

    def GetSelection(self) -> _FakeSelection:  # noqa: N802 -- wx API name the SUT calls
        """Report the scripted selection."""
        return self._selection


class _FakeIssuesModel:
    """An ``IssuesListModel`` double with a scripted row and count."""

    def __init__(self, *, row: int, count: int) -> None:
        """Report *row* for the item and *count* rows overall."""
        self._row = row
        self._count = count

    def GetRow(self, _item: object) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Report the scripted row index."""
        return self._row

    def GetCount(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Report the scripted row count."""
        return self._count


class _RecordingIssuesPresenter:
    """A presenter double recording selection notifications only."""

    def __init__(self) -> None:
        """Start with an empty call log."""
        self.calls: list[tuple[str, int | None]] = []

    def on_row_selected(self, row: int) -> None:
        """Record a forwarded row selection."""
        self.calls.append(("on_row_selected", row))

    def on_nothing_selected(self) -> None:
        """Record a forwarded no-selection notice."""
        self.calls.append(("on_nothing_selected", None))

    def refresh(self) -> None:
        """Record a refresh; reconcile must never make one."""
        self.calls.append(("refresh", None))


def _issues_view(
    *, selection_ok: bool, row: int, count: int
) -> tuple[RiderIssuesView, _RecordingIssuesPresenter]:
    """Return a real ``RiderIssuesView`` wired to recording doubles.

    Built with ``object.__new__`` like ``_view_over``: the reconcile
    seam touches only ``issues_list``, ``_model`` and ``presenter``,
    so no desktop (and no ``__init__`` binding) is needed.
    """
    view = object.__new__(RiderIssuesView)
    view.issues_list = _FakeIssuesList(_FakeSelection(ok=selection_ok))
    view._model = _FakeIssuesModel(row=row, count=count)
    presenter = _RecordingIssuesPresenter()
    view.presenter = presenter
    return view, presenter


@pytest.mark.parametrize(("row", "count"), [(0, 1), (2, 3)])
def test_issues_view_reconcile_given_a_valid_selection_forwards_the_row(
    row: int, count: int
) -> None:
    """A live in-range selection is forwarded to the presenter."""
    view, presenter = _issues_view(selection_ok=True, row=row, count=count)

    view._reconcile_selection()

    assert presenter.calls == [("on_row_selected", row)]


@pytest.mark.parametrize(
    ("selection_ok", "row", "count"),
    [
        (False, 0, 3),  # T-3: nothing selected
        (True, -1, 3),  # T-4: min - 1
        (True, 3, 3),  # T-4: max + 1
    ],
)
def test_issues_view_reconcile_given_no_valid_row_notifies_nothing_selected(
    selection_ok: bool,  # noqa: FBT001 -- a parametrize row value, not a call-site flag
    row: int,
    count: int,
) -> None:
    """No live in-range row disables through on_nothing_selected."""
    view, presenter = _issues_view(selection_ok=selection_ok, row=row, count=count)

    view._reconcile_selection()

    assert presenter.calls == [("on_nothing_selected", None)]


def test_issues_view_reconcile_never_refreshes_the_report() -> None:
    """Reconcile must not recurse through refresh (D1)."""
    view, presenter = _issues_view(selection_ok=True, row=0, count=1)

    view._reconcile_selection()

    assert ("refresh", None) not in presenter.calls
