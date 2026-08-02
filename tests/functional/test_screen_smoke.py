# SPDX-License-Identifier: GPL-3.0-only
"""The forever-test (E1.3.3): every screen loads, shows, and closes.

project-plan.md section 4: "one parametrized test over all 23
windows -- load from XRC, Show(), assert every section-15b name
resolves, capture a screenshot artifact, close cleanly. It runs in
every CI build forever after." This module is that test, plus the
harness-isolation and negative-path proofs the brief calls for.

Two frames (``main_frame``, ``results_frame``) load with
``LoadFrame``; the other 21 load with ``LoadDialog`` --
:data:`pages.WINDOWS` carries that distinction per window.
``main_menubar`` is a menu-bar resource, not a window: it is
deliberately absent from :data:`pages.WINDOWS`, and its own
non-resolution is asserted directly rather than silently omitted.
"""

import re
from pathlib import Path

import harness
import pages
import pytest

from rivercrossing.ui import ids

pytestmark = pytest.mark.functional

SCREENSHOT_DIR = Path(__file__).resolve().parent / "_screenshots"


def _missing_controls(window: object, controls: tuple[str, ...]) -> tuple[str, ...]:
    """Return every name in *controls* that fails to resolve."""
    missing = []
    for name in controls:
        try:
            harness.find_control(window, name)
        except harness.ControlNotFoundError:
            missing.append(name)
    return tuple(missing)


def _unplaced_buttons(window: object, buttons: tuple[str, ...]) -> tuple[str, ...]:
    """Return every button name in *buttons* still sitting at (-1, -1).

    xrc-windows.md / library.xrc's own header: a wxStdDialogButtonSizer
    only positions the stock ids its AddButton() recognises. A custom
    button dropped into one resolves by name but is never added to
    the sizer, so it stays at (-1, -1) -- invisible, though present.
    This already reached us once; it is what this check guards.
    """
    unplaced = []
    for name in buttons:
        control = harness.find_control(window, name)
        if control.GetPosition() == (-1, -1):
            unplaced.append(name)
    return tuple(unplaced)


def test_window_registry_declares_all_twenty_three_windows() -> None:
    """A window disappearing must shrink this, not the suite."""
    assert len(pages.WINDOWS) == 23


@pytest.mark.parametrize("spec", pages.WINDOWS, ids=lambda spec: spec.name)
def test_screen_loads_shows_resolves_names_places_buttons_and_closes_cleanly(
    spec: pages.WindowSpec, xrc_resource: object
) -> None:
    """Every frozen window: names resolve, buttons placed, it closes."""
    window = harness.load_window(xrc_resource, spec.name, frame=spec.is_frame)
    window.Show()
    window.Layout()
    harness.pump()

    try:
        missing = _missing_controls(window, spec.controls)
        unplaced = _unplaced_buttons(window, spec.buttons)
        saved = harness.screenshot(window, SCREENSHOT_DIR / f"{spec.name}.png")
    finally:
        closed = harness.close_window(window)

    assert missing == ()
    assert unplaced == ()
    assert saved.exists()
    assert closed is True


def test_main_menubar_does_not_resolve_as_a_window_control(xrc_resource: object) -> None:
    """Measured: the menu-bar XRC handler drops the name on attach."""
    frame = harness.load_window(xrc_resource, ids.MAIN_FRAME, frame=True)
    menubar = harness.load_menubar(xrc_resource, ids.MAIN_MENUBAR)
    frame.SetMenuBar(menubar)
    harness.pump()

    try:
        with pytest.raises(harness.ControlNotFoundError, match=re.escape(ids.MAIN_MENUBAR)):
            harness.find_control(frame, ids.MAIN_MENUBAR)
    finally:
        harness.close_window(frame)


def test_close_window_reaps_the_dialog_so_a_shared_name_no_longer_resolves(
    xrc_resource: object,
) -> None:
    """Isolation proof: a later case must not see an earlier control.

    ``plate_input`` is a name shared by four windows. Without the
    ``Destroy()`` + pump ``close_window`` performs, a destroyed-but-
    unreaped dialog keeps answering ``FindWindowByName`` in the same
    process (measured) and would leak into whichever later
    parametrized case happens to load next.
    """
    dialog = harness.load_window(xrc_resource, ids.MANUAL_DEAL_DLG, frame=False)
    dialog.Show()
    harness.pump()
    control = harness.find_control(dialog, ids.PLATE_INPUT)
    assert control.GetName() == ids.PLATE_INPUT

    harness.close_window(dialog)

    residual = harness.wx.Window.FindWindowByName(ids.PLATE_INPUT)
    assert residual is None


def test_type_text_given_a_string_updates_the_controls_value(xrc_resource: object) -> None:
    """Direct injection is measured reliable; the simulator is not."""
    dialog = harness.load_window(xrc_resource, ids.RIDE_SETUP_DLG, frame=False)
    dialog.Show()
    harness.pump()

    try:
        harness.type_text(dialog, ids.NAME_INPUT, "GORBA EPIC 2026")
        value = harness.find_control(dialog, ids.NAME_INPUT).GetValue()
    finally:
        harness.close_window(dialog)

    assert value == "GORBA EPIC 2026"


def test_click_given_a_stock_button_fires_its_bound_handler(xrc_resource: object) -> None:
    """Direct event injection delivers a real EVT_BUTTON (measured)."""
    dialog = harness.load_window(xrc_resource, ids.STOP_CONFIRM_DLG, frame=False)
    dialog.Show()
    harness.pump()
    fired_ids = []
    ok_button = harness.find_control(dialog, pages.WX_ID_OK)
    expected_id = ok_button.GetId()
    dialog.Bind(harness.wx.EVT_BUTTON, lambda evt: fired_ids.append(evt.GetId()), ok_button)

    try:
        harness.click(dialog, pages.WX_ID_OK)
    finally:
        harness.close_window(dialog)

    assert fired_ids == [expected_id]


def test_run_modal_given_a_dismiss_id_returns_it_from_showmodal(xrc_resource: object) -> None:
    """The dialog-hook: ShowModal ends on its own, no user present."""
    dialog = harness.load_window(xrc_resource, ids.FINISH_CONFIRM_DLG, frame=False)

    try:
        result = harness.run_modal(dialog, dismiss_with=harness.wx.ID_CANCEL)
    finally:
        closed = harness.close_window(dialog)

    assert result == harness.wx.ID_CANCEL
    assert closed is True


def test_load_window_given_an_unknown_frame_name_raises_naming_the_lookup(
    xrc_resource: object,
) -> None:
    """T-5: the ``LoadFrame`` branch of ``load_window``'s raise."""
    expected = re.escape("LoadFrame(None, 'no_such_frame')")
    with pytest.raises(harness.WindowLoadError, match=expected):
        harness.load_window(xrc_resource, "no_such_frame", frame=True)


def test_load_window_given_an_unknown_dialog_name_raises_naming_the_lookup(
    xrc_resource: object,
) -> None:
    """T-5: the ``LoadDialog`` branch of ``load_window``'s raise."""
    expected = re.escape("LoadDialog(None, 'no_such_dlg')")
    with pytest.raises(harness.WindowLoadError, match=expected):
        harness.load_window(xrc_resource, "no_such_dlg", frame=False)


def test_load_menubar_given_an_unknown_name_raises_naming_the_lookup(
    xrc_resource: object,
) -> None:
    """T-5: ``load_menubar``'s raise."""
    expected = re.escape("LoadMenuBar(None, 'no_such_menubar')")
    with pytest.raises(harness.WindowLoadError, match=expected):
        harness.load_menubar(xrc_resource, "no_such_menubar")


def test_find_control_given_an_unknown_name_raises_naming_window_and_control(
    xrc_resource: object,
) -> None:
    """T-5: ``find_control``'s raise names the window and the miss."""
    dialog = harness.load_window(xrc_resource, ids.RIDE_SETUP_DLG, frame=False)

    try:
        with pytest.raises(
            harness.ControlNotFoundError,
            match=re.escape("'ride_setup_dlg' has no control named 'no_such_control'"),
        ):
            harness.find_control(dialog, "no_such_control")
    finally:
        harness.close_window(dialog)


def test_screenshot_given_an_unwritable_destination_raises_naming_it(
    xrc_resource: object, tmp_path: Path
) -> None:
    """T-5: ``screenshot``'s raise, forced by a directory as target."""
    dialog = harness.load_window(xrc_resource, ids.ABOUT_DLG, frame=False)
    dialog.Show()
    harness.pump()
    blocked_destination = tmp_path / "shot.png"
    blocked_destination.mkdir()

    try:
        with pytest.raises(harness.ScreenshotError, match=re.escape(str(blocked_destination))):
            harness.screenshot(dialog, blocked_destination)
    finally:
        harness.close_window(dialog)
