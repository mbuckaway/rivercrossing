# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the app-side Fault-B guard (degraded XRC load).

Production mirror of ``harness.load_window_verified``: under worker
load the process-global ``wx.xrc.XmlResource`` singleton can silently
skip a subtree during a load, so ``main_frame`` comes back missing a
control ``MainFrame.__init__`` later ``_find``s -- which surfaces as a
bare ``LookupError`` from ``ui.views._support.find_control``. The
app-side fix (``ui.app._load_frame_verified``) verifies the frame's
required controls and rebuilds once from a fresh private resource.

The verify is a CONCRETE-class check, not a name-only check: under the
wx/SIP wrapper-cache corruption ``FindWindowByName`` can answer a
NON-None stale wrapper of the WRONG Python type for a live control, so
a name-only ``is None`` check false-fasts a degraded load and the ctor
later raises. The app gate checks each name resolves to the exact class
``MainFrame.__init__`` demands, with the bounded
``del control; gc.collect()`` settle (never a ``SafeYield``).

These tests never construct a real window: ``FindWindowByName`` is
monkeypatched to simulate a skipped subtree or a stale wrapper, and the
frame/resource objects are ``MagicMock``s, so the verify/rebuild
decision logic runs headless (the same reason ``test_view_support.py``
carries no window).
"""

import re
from unittest.mock import MagicMock

import pytest
import wx
import wx.dataview

from rivercrossing.ui import app, ids
from rivercrossing.ui.views.main_frame import REQUIRED_CONTROL_CLASSES, REQUIRED_CONTROLS


class _FakeControl:
    """A headless stand-in for a correctly-typed control wrapper.

    ``isinstance`` is what the app gate checks, so the tests pass this
    plain class (and instances of it) as the expected class/return of a
    monkeypatched ``FindWindowByName`` -- no real wx window is built.
    """


def test_required_controls_lists_exactly_the_init_find_controls() -> None:
    """The verify tuple is the single source for __init__'s 31 finds.

    Pins the contract so the guard can never silently drift from
    ``MainFrame.__init__``: if a control is added/removed there without
    updating this tuple, this test fails.
    """
    assert REQUIRED_CONTROLS == (
        ids.CROSSINGS_LIST,
        ids.MAIN_SPLITTER,
        ids.PLATE_INPUT,
        ids.RECORD_BTN,
        ids.LAST_CROSSING_LBL,
        # C1/§5: the ride-info group -- the logo, then the six
        # read-only values (name/date/venue/organizer/scorer/lap km).
        ids.RIDE_LOGO_BMP,
        ids.RIDE_NAME_VALUE,
        ids.RIDE_DATE_VALUE,
        ids.RIDE_VENUE_VALUE,
        ids.RIDE_ORGANIZER_VALUE,
        ids.RIDE_SCORER_VALUE,
        ids.RIDE_LAP_KM_VALUE,
        ids.RIDE_STATUS_LBL,
        ids.CROSSINGS_COUNT_LBL,
        ids.CARDS_COUNT_LBL,
        ids.ON_COURSE_LBL,
        ids.SHOE_LBL,
        # W12: the two registration chips join the four live counters.
        ids.RIDERS_COUNT_LBL,
        ids.TEAMS_COUNT_LBL,
        ids.START_BTN,
        ids.STOP_BTN,
        ids.UNDO_BTN,
        # WS-D/WS-H (ux-polish): the code-side gauge slots, the
        # notebook shell and its Riders page join the init finds.
        ids.ELAPSED_CLOCK_PANEL,
        ids.REMAINING_CLOCK_PANEL,
        ids.RIDE_STATUS_PANEL,
        ids.CLOCK_ELAPSED_LBL,
        ids.CLOCK_REMAINING_LBL,
        ids.REVIEW_NOTEBOOK,
        ids.FLAGGED_LIST,
        ids.REVIEW_BTN,
        ids.CONSOLE_RIDERS_LIST,
    )


def test_required_control_classes_cover_exactly_the_required_controls() -> None:
    """The class map's keys are exactly REQUIRED_CONTROLS (lockstep)."""
    assert set(REQUIRED_CONTROL_CLASSES) == set(REQUIRED_CONTROLS)


def test_required_control_classes_transcribe_the_init_find_calls() -> None:
    """The map pins the ctor's ``_find`` calls verbatim."""
    expected = {
        ids.CROSSINGS_LIST: wx.dataview.DataViewCtrl,
        ids.MAIN_SPLITTER: wx.SplitterWindow,
        ids.PLATE_INPUT: wx.TextCtrl,
        ids.RECORD_BTN: wx.Button,
        ids.LAST_CROSSING_LBL: wx.StaticText,
        ids.RIDE_LOGO_BMP: wx.StaticBitmap,
        ids.RIDE_NAME_VALUE: wx.TextCtrl,
        ids.RIDE_DATE_VALUE: wx.TextCtrl,
        ids.RIDE_VENUE_VALUE: wx.TextCtrl,
        ids.RIDE_ORGANIZER_VALUE: wx.TextCtrl,
        ids.RIDE_SCORER_VALUE: wx.TextCtrl,
        ids.RIDE_LAP_KM_VALUE: wx.TextCtrl,
        ids.RIDE_STATUS_LBL: wx.StaticText,
        ids.CROSSINGS_COUNT_LBL: wx.StaticText,
        ids.CARDS_COUNT_LBL: wx.StaticText,
        ids.ON_COURSE_LBL: wx.StaticText,
        ids.SHOE_LBL: wx.StaticText,
        ids.RIDERS_COUNT_LBL: wx.StaticText,
        ids.TEAMS_COUNT_LBL: wx.StaticText,
        ids.START_BTN: wx.BitmapButton,
        ids.STOP_BTN: wx.BitmapButton,
        ids.UNDO_BTN: wx.Button,
        ids.ELAPSED_CLOCK_PANEL: wx.Panel,
        ids.REMAINING_CLOCK_PANEL: wx.Panel,
        ids.RIDE_STATUS_PANEL: wx.Panel,
        ids.CLOCK_ELAPSED_LBL: wx.StaticText,
        ids.CLOCK_REMAINING_LBL: wx.StaticText,
        ids.REVIEW_NOTEBOOK: wx.Notebook,
        ids.FLAGGED_LIST: wx.dataview.DataViewCtrl,
        ids.REVIEW_BTN: wx.Button,
        ids.CONSOLE_RIDERS_LIST: wx.dataview.DataViewCtrl,
    }
    assert expected == REQUIRED_CONTROL_CLASSES


def test_missing_required_control_returns_none_when_all_controls_resolve(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A frame where every required name resolves is complete."""
    required = (ids.CROSSINGS_LIST, ids.MAIN_SPLITTER)
    classes = {ids.CROSSINGS_LIST: _FakeControl, ids.MAIN_SPLITTER: _FakeControl}
    frame = MagicMock()
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        lambda _name, _parent=None: _FakeControl(),
    )

    assert app._missing_required_control(frame, required, classes) is None


def test_missing_required_control_returns_the_first_skipped_control_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The first name XRC skipped (does not resolve) is reported."""
    required = (ids.CROSSINGS_LIST, ids.LAST_CROSSING_LBL)
    classes = {ids.CROSSINGS_LIST: _FakeControl, ids.LAST_CROSSING_LBL: _FakeControl}
    frame = MagicMock()
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        lambda name, _parent=None: None if name == ids.LAST_CROSSING_LBL else _FakeControl(),
    )

    assert app._missing_required_control(frame, required, classes) == ids.LAST_CROSSING_LBL


def test_missing_required_control_reports_a_wrong_typed_wrapper_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-None, wrong-typed wrapper counts as missing."""
    required = (ids.CROSSINGS_LIST,)
    classes = {ids.CROSSINGS_LIST: _FakeControl}
    frame = MagicMock()
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        lambda _name, _parent=None: object(),
    )

    assert app._missing_required_control(frame, required, classes) == ids.CROSSINGS_LIST


def test_missing_required_control_settles_a_transiently_wrong_wrapper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lookup wrong only briefly settles into its expected class."""
    required = (ids.CROSSINGS_LIST,)
    classes = {ids.CROSSINGS_LIST: _FakeControl}
    frame = MagicMock()
    lookups = 0

    def _find(_name: str, _parent: object = None) -> object:
        nonlocal lookups
        lookups += 1
        return object() if lookups < 3 else _FakeControl()

    monkeypatch.setattr(wx.Window, "FindWindowByName", _find)

    assert app._missing_required_control(frame, required, classes) is None
    assert lookups == 3


def test_missing_required_control_returns_none_for_an_empty_contract() -> None:
    """An empty required tuple means the frame is trivially complete."""
    frame = MagicMock()

    assert app._missing_required_control(frame, (), {}) is None


def test_load_frame_verified_returns_the_first_frame_when_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A complete singleton load returns untouched (no rebuild)."""
    required = (ids.CROSSINGS_LIST,)
    classes = {ids.CROSSINGS_LIST: _FakeControl}
    frame = MagicMock()
    resource = MagicMock()
    resource.LoadFrame.return_value = frame
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        lambda _name, _parent=None: _FakeControl(),
    )

    def _fresh_must_not_run() -> None:
        raise AssertionError("a complete frame must not trigger a rebuild")

    monkeypatch.setattr(app, "_fresh_xrc_resource", _fresh_must_not_run)

    result = app._load_frame_verified(resource, required, classes)

    assert result is frame
    resource.LoadFrame.assert_called_once_with(None, ids.MAIN_FRAME)
    frame.Destroy.assert_not_called()


def test_load_frame_verified_rebuilds_once_from_a_fresh_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded singleton load is rebuilt once from a fresh resource.

    The rebuilt frame is returned; the degraded frame is destroyed only
    after the fresh build (mirroring the harness ordering).
    """
    required = (ids.CROSSINGS_LIST,)
    classes = {ids.CROSSINGS_LIST: _FakeControl}
    degraded = MagicMock()
    rebuilt = MagicMock()
    resource = MagicMock()
    resource.LoadFrame.return_value = degraded
    fresh = MagicMock()
    fresh.LoadFrame.return_value = rebuilt
    monkeypatch.setattr(app, "_fresh_xrc_resource", lambda: fresh)
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        lambda _name, parent=None: None if parent is degraded else _FakeControl(),
    )

    result = app._load_frame_verified(resource, required, classes)

    assert result is rebuilt
    resource.LoadFrame.assert_called_once_with(None, ids.MAIN_FRAME)
    fresh.LoadFrame.assert_called_once_with(None, ids.MAIN_FRAME)
    degraded.Destroy.assert_called_once_with()
    rebuilt.Destroy.assert_not_called()


def test_load_frame_verified_rebuilds_when_a_control_is_the_wrong_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A wrong-typed wrapper triggers the rebuild."""
    required = (ids.CROSSINGS_LIST,)
    classes = {ids.CROSSINGS_LIST: _FakeControl}
    degraded = MagicMock()
    rebuilt = MagicMock()
    resource = MagicMock()
    resource.LoadFrame.return_value = degraded
    fresh = MagicMock()
    fresh.LoadFrame.return_value = rebuilt
    monkeypatch.setattr(app, "_fresh_xrc_resource", lambda: fresh)
    monkeypatch.setattr(
        wx.Window,
        "FindWindowByName",
        lambda _name, parent=None: object() if parent is degraded else _FakeControl(),
    )

    result = app._load_frame_verified(resource, required, classes)

    assert result is rebuilt
    resource.LoadFrame.assert_called_once_with(None, ids.MAIN_FRAME)
    fresh.LoadFrame.assert_called_once_with(None, ids.MAIN_FRAME)
    degraded.Destroy.assert_called_once_with()
    rebuilt.Destroy.assert_not_called()


def test_load_frame_verified_raises_when_rebuild_is_still_incomplete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A still-incomplete rebuild raises the find_control error."""
    required = (ids.CROSSINGS_LIST, ids.LAST_CROSSING_LBL)
    classes = {ids.CROSSINGS_LIST: _FakeControl, ids.LAST_CROSSING_LBL: _FakeControl}
    degraded = MagicMock()
    rebuilt = MagicMock()
    rebuilt.GetName.return_value = ids.MAIN_FRAME
    rebuilt.GetChildren.return_value = []
    resource = MagicMock()
    resource.LoadFrame.return_value = degraded
    fresh = MagicMock()
    fresh.LoadFrame.return_value = rebuilt
    monkeypatch.setattr(app, "_fresh_xrc_resource", lambda: fresh)
    monkeypatch.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: None)

    with pytest.raises(
        LookupError,
        match=re.escape(
            f"{ids.MAIN_FRAME} has no control named {ids.CROSSINGS_LIST!r} "
            "(first-level children: 0 -- [])"
        ),
    ):
        app._load_frame_verified(resource, required, classes)

    degraded.Destroy.assert_called_once_with()
    rebuilt.Destroy.assert_called_once_with()


def test_load_frame_verified_raises_when_the_fresh_resource_has_no_frame(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A fresh resource that cannot rebuild raises an error."""
    required = (ids.CROSSINGS_LIST,)
    classes = {ids.CROSSINGS_LIST: _FakeControl}
    degraded = MagicMock()
    resource = MagicMock()
    resource.LoadFrame.return_value = degraded
    fresh = MagicMock()
    fresh.LoadFrame.return_value = None
    monkeypatch.setattr(app, "_fresh_xrc_resource", lambda: fresh)
    monkeypatch.setattr(wx.Window, "FindWindowByName", lambda _name, _parent=None: None)

    with pytest.raises(
        LookupError,
        match=re.escape(f"fresh XmlResource found no window named {ids.MAIN_FRAME!r} to rebuild"),
    ):
        app._load_frame_verified(resource, required, classes)

    degraded.Destroy.assert_called_once_with()


def test_load_xrc_resources_memoizes_the_global_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The process-global XRC resource is loaded once and reused.

    Re-loading on every ``build_main_window`` call re-parses the .xrc
    files, and a later re-parse can re-roll the Fault-B degradation
    the guard exists to work around. The app loader memoizes the
    singleton, matching the harness's load-once pattern.
    """
    import wx.xrc  # noqa: PLC0415 -- submodule, not loaded by plain `import wx`

    monkeypatch.setattr(app, "_loaded_xrc_resource", None)
    get_calls = 0

    def _fake_get() -> MagicMock:
        nonlocal get_calls
        get_calls += 1
        return MagicMock()

    monkeypatch.setattr(wx.xrc.XmlResource, "Get", staticmethod(_fake_get))

    first = app._load_xrc_resources()
    second = app._load_xrc_resources()

    assert first is second
    assert get_calls == 1
