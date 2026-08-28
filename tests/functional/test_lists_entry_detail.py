# SPDX-License-Identifier: GPL-3.0-only
"""Real-toolkit tests for ``entry_detail_dlg`` (E1.5.2, E5.4.2).

Split out of ``test_lists_demo.py`` -- alongside
``test_lists_results.py`` -- so the two heaviest functional files
spread their per-worker window churn across ``--dist loadfile``
workers (the wrapper-cache corruption remedy). ``EntryDetailDialog``
decorates an already-XRC-loaded window (``harness.load_window``, the
same pattern ``test_console_demo.py`` uses for ``MainFrame``) with
the code-side bindings xrc-windows.md's per-window footnotes assign
to it: each DataView's columns and rows and ``entry_detail_dlg``'s
two card bitmap columns.

E5.4.2 retired the demo seam from the app path: the app's entry-detail
route now opens the empty state (``EmptyDataSource`` -- no store-backed
entry is selected until E7 wires the real lookup), so the two canvas-row
pins below assert that empty state against their own empty-fed dialogs.
The shared ``shared_entry_detail`` fixture keeps demo's populated rows
for the view-capability assertions (bitmap rendering, card-imagelist
cache) -- ``rivercrossing.demo`` remains importable from tests.

The window carries no splitter, so the rebuild-and-compare hazard
this suite's harness warns about does not apply; it is built exactly
once per module and never torn down and rebuilt mid-module. The
constants and helpers shared with the other two list-window files
live in ``_lists_common``.
"""

import re
from typing import Any

import harness
import pytest
from _lists_common import (
    CANVAS_CARDS_HELD_KEYS,
    CANVAS_LAPS_CARD_KEYS,
    MAX_SCREEN_HEIGHT,
    MAX_SCREEN_WIDTH,
    _model_row,
    _spy_repaint,
)

from rivercrossing.demo import DemoDataSource
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.data_source import EmptyDataSource
from rivercrossing.ui.views import _support, entry_detail
from rivercrossing.ui.views.entry_detail import COL_CARD as LAPS_COL_CARD
from rivercrossing.ui.views.entry_detail import EntryDetailDialog

pytestmark = pytest.mark.functional


# ----------------------------------------------------------- fixtures


@pytest.fixture(scope="module")
def shared_entry_detail(xrc_resource: object) -> EntryDetailDialog:
    """One ``EntryDetailDialog``, demo-fed for the view-capability rows.

    Kept populated (demo fixture, tests-only since E5.4.2) so the
    bitmap-rendering and card-imagelist assertions below have rows to
    render; the app-path pins build their own empty-fed dialogs.
    """
    window = harness.load_window_verified(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = EntryDetailDialog(window, "77", data_source=DemoDataSource())
        yield view
    finally:
        # Phase 2 reference hygiene: drop the view before the window
        # dies (see test_console_demo.py's shared_console finally).
        del view
        harness.close_window(window)


def _show_empty_detail(xrc_resource: object) -> tuple[Any, EntryDetailDialog]:
    """Load ``entry_detail_dlg`` wired to the E5.4.2 empty state."""
    window = harness.load_window_verified(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    try:
        window.Show()
        window.Layout()
        harness.pump()
        view = EntryDetailDialog(window, "", data_source=EmptyDataSource())
    except Exception:
        harness.close_window(window)
        raise
    return window, view


# --------------------------------------------------- entry_detail_dlg


def test_entry_detail_given_an_empty_source_shows_an_empty_header_and_members(
    xrc_resource: object,
) -> None:
    """E5.4.2: no store-backed entry selected -- header/members empty.

    E7 wires the real per-entry lookup (R-38's deep-link); until then
    the dialog opens showing the empty state rather than demo rows.
    """
    window, view = _show_empty_detail(xrc_resource)

    try:
        header = view.entry_header_lbl.GetLabelText()
        members = view.members_lbl.GetLabelText()
    finally:
        harness.close_window(window)

    assert (header, members) == ("", "")


def test_entry_detail_given_an_empty_source_shows_no_laps(
    xrc_resource: object,
) -> None:
    """E5.4.2: the empty entry has no lap history rows."""
    window, view = _show_empty_detail(xrc_resource)

    try:
        model = view.laps_list.GetModel()
        rows = tuple(_model_row(model, row, range(4)) for row in range(model.GetCount()))
    finally:
        harness.close_window(window)

    assert rows == ()


def test_entry_detail_laps_list_card_column_renders_the_dealt_bitmaps(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """Req 4: a real imagelist bitmap, not just a typed column."""
    model = shared_entry_detail.laps_list.GetModel()

    rendered = tuple(model.GetValueByRow(row, LAPS_COL_CARD) for row in range(model.GetCount()))

    assert rendered == tuple(
        shared_entry_detail.card_images.bitmap(key) for key in CANVAS_LAPS_CARD_KEYS
    )


def test_entry_detail_cards_list_renders_the_held_card_bitmaps(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """Req 4: cards_list's one column is a real bitmap per held card."""
    model = shared_entry_detail.cards_list.GetModel()

    rendered = tuple(model.GetValueByRow(row, 0) for row in range(model.GetCount()))

    assert rendered == tuple(
        shared_entry_detail.card_images.bitmap(key) for key in CANVAS_CARDS_HELD_KEYS
    )


def test_entry_detail_card_images_defaults_to_the_shared_support_cache(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """The extracted ``_support.default_card_images`` backs this deck.

    Also proves the merge's real effect: before extraction,
    ``main_frame.py`` and ``entry_detail.py`` each cached their own
    separate 53-bitmap deck; ``test_console_demo.py``'s equivalent
    assertion for ``shared_console`` shares this exact object.
    """
    assert shared_entry_detail.card_images is _support.default_card_images()


def test_entry_detail_given_an_unknown_plate_raises_naming_it(xrc_resource: object) -> None:
    """T-5: ``DemoDataSource.entry_detail``'s only ``raise``."""
    window = harness.load_window_verified(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    try:
        window.Show()
        harness.pump()
        expected = re.escape("no entry detail for plate 'no-such-plate'")
        with pytest.raises(LookupError, match=expected):
            EntryDetailDialog(window, "no-such-plate", data_source=DemoDataSource())
    finally:
        harness.close_window(window)


def test_entry_detail_fits_within_1366x768(shared_entry_detail: EntryDetailDialog) -> None:
    """UX-DESKTOP §6: every window must fit the field-laptop floor."""
    size = shared_entry_detail.dialog.GetSize()

    assert size.width <= MAX_SCREEN_WIDTH
    assert size.height <= MAX_SCREEN_HEIGHT


def test_entry_detail_applies_the_measured_minimum_width(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """D16: no canvas width is stated; the floor is now forced.

    Measured on windows-latest CI (run 31015653629): Fit() alone
    landed on 650px there, Segoe UI's button metrics fitting
    narrower than macOS's Fit()-derived 726px. The width is now
    forced before Fit(), exactly like ride_library/rider_editor/
    results_win already force their canvas widths (D16). This
    assertion's red state is only visible on windows-latest: on
    macOS, Fit() alone already lands on entry_detail.MIN_SIZE[0],
    so nothing appears to change locally -- the accepted repo
    precedent for a platform-only fix.
    """
    size = shared_entry_detail.dialog.GetSize()

    assert size.width == entry_detail.MIN_SIZE[0]


# ------------------------------------------------- negative path: _find


def test_entry_detail_find_given_an_unknown_control_name_raises_naming_it(
    shared_entry_detail: EntryDetailDialog,
) -> None:
    """T-5: the one ``raise`` in ``views/entry_detail.py``."""
    with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
        shared_entry_detail._find("no_such_control")


# ------------------------------ repaint after model (unverified remedy)


def test_entry_detail_show_entry_repaints_both_dataviews_after_associating_models(
    xrc_resource: object,
) -> None:
    """Unverified remedy; see ``associate_model``'s docstring.

    ``show_entry`` associates two separate models (cards_list,
    laps_list) in one call -- both must repaint.
    """
    window = harness.load_window_verified(xrc_resource, ids.ENTRY_DETAIL_DLG, frame=False)
    try:
        window.Show()
        harness.pump()
        # Both kept alive: _spy_repaint's docstring.
        cards_control = harness.find_control(window, ids.CARDS_LIST)
        laps_control = harness.find_control(window, ids.LAPS_LIST)
        cards_refresh, cards_update = _spy_repaint(cards_control)
        laps_refresh, laps_update = _spy_repaint(laps_control)
        view = EntryDetailDialog(window, "", data_source=EmptyDataSource())
        cards_count = view.cards_list.GetModel().GetCount()
        laps_count = view.laps_list.GetModel().GetCount()
    finally:
        harness.close_window(window)

    assert (cards_count, laps_count) == (0, 0)
    cards_refresh.assert_called_once_with()
    cards_update.assert_called_once_with()
    laps_refresh.assert_called_once_with()
    laps_update.assert_called_once_with()
