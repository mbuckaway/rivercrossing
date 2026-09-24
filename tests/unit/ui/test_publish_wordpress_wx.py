# SPDX-License-Identifier: GPL-3.0-only
"""Real-XRC pins for the publish dialog's "What to publish" dropdown.

``PublishWordpressDialog`` is thin glue over the presenter's own
``PUBLISH_KIND_CHOICES``/``kind_value``/``default_title``/
``default_slug``, but the wiring itself can only fail against a real
window: ``self._find(ids.WP_KIND_CHOICE, wx.Choice)`` raises when the
authored control is missing or is the wrong class, and the change
handler is only exercised by a real ``wx.Choice``. These tests
therefore load the shipped ``publish_wordpress_dlg`` from the packaged
``.xrc`` and drive the view over it -- the pattern
``test_xrc_load_selfheal.py`` uses for the simulator dialogs, with a
live ``wx.App`` kept in a module cache (an unbound ``wx.App()`` is
collected as soon as its fixture goes out of scope and the interpreter
then hangs at exit).

The controls are read back through
:func:`~rivercrossing.ui.views._support.find_control`, the same scoped
settling walk the view resolves them with, never
``wx.Window.FindWindowByName``: once this module has built more than
one XRC window, a bare ``FindWindowByName`` was measured to answer
with the *earlier* window's control (the SIP address-reuse poison
``find_control``'s own docstring describes), which reads as an empty
field. The dialogs are destroyed together when the module's tests end
rather than per test, for the same reason.
"""

from dataclasses import replace
from functools import cache
from typing import TYPE_CHECKING, Any

import pytest
import wx

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.publish_wordpress import default_slug, default_title
from rivercrossing.ui.presenters.settings import default_settings
from rivercrossing.ui.views import _support
from rivercrossing.ui.views.publish_wordpress import (
    PublishRunningDialog,
    PublishWordpressDialog,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

# The live ride name the dialog derives its title and slug from, and
# the two dropdown labels (PUBLISH_KIND_CHOICES' own render order).
_RIDE_NAME = "GORBA EPIC 2026"
_FULL_LABEL = "Full results"
_PODIUM_LABEL = "Podium results"

# Every dialog this module built, in construction order.
_WINDOWS: list[wx.Dialog] = []


@cache
def _process_wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Return the process's one wx.App, creating it on first use.

    Reuses an app another unit module already built (``wx.GetApp()``
    answers it then): this module is not the only one that needs an app,
    and destroying the process's app breaks every later test that needs
    one.
    """
    return wx.GetApp() or wx.App(False)  # noqa: FBT003 -- wx's own signature: redirect=False


@pytest.fixture(scope="module")
def wx_app() -> Any:  # noqa: ANN401 -- wx ships no stubs; Any is honest
    """Guarantee a live wx.App before any real XRC object is built."""
    return _process_wx_app()


@pytest.fixture(scope="module", autouse=True)
def _destroy_windows(wx_app: Any) -> Iterator[None]:  # noqa: ANN401, ARG001 -- ordering only
    """Destroy every window this module built, once its tests end."""
    yield
    for window in _WINDOWS:
        window.Destroy()
    _WINDOWS.clear()


@pytest.fixture
def dialog(wx_app: Any) -> wx.Dialog:  # noqa: ANN401, ARG001 -- ordering only
    """Load the shipped ``publish_wordpress_dlg`` for one test."""
    window: wx.Dialog = _support.fresh_resource().LoadDialog(None, ids.PUBLISH_WORDPRESS_DLG)
    _WINDOWS.append(window)
    return window


def _view(dialog: wx.Dialog, *, wp_kind: str) -> PublishWordpressDialog:
    """Decorate *dialog* with the app's own settings for *wp_kind*."""
    return PublishWordpressDialog(
        dialog,
        settings=replace(default_settings(), wp_kind=wp_kind),
        ride_name=_RIDE_NAME,
        on_publish=lambda _form: None,
    )


def _kind_label(dialog: wx.Dialog) -> str:
    """Return ``wp_kind_choice``'s selected item label."""
    choice = _support.find_control(dialog, ids.WP_KIND_CHOICE, wx.Choice)
    return str(choice.GetStringSelection())


def _text(dialog: wx.Dialog, name: str) -> str:
    """Return the current text of the named text box in *dialog*."""
    control = _support.find_control(dialog, name, wx.TextCtrl)
    return str(control.GetValue())


def test_fresh_resource_given_the_packaged_dir_resolves_the_publish_dialog(
    dialog: wx.Dialog,
) -> None:
    """The shipped publish form loads under its frozen name (R-05)."""
    assert isinstance(dialog, wx.Dialog)
    assert dialog.GetName() == ids.PUBLISH_WORDPRESS_DLG


def test_publish_dialog_kind_choice_resolves_as_a_choice(dialog: wx.Dialog) -> None:
    """The frozen name resolves to a real wxChoice, not a Control."""
    control = _support.find_control(dialog, ids.WP_KIND_CHOICE, wx.Choice)

    assert isinstance(control, wx.Choice)


def test_publish_dialog_init_given_a_podium_setting_shows_the_podium_page_name(
    dialog: wx.Dialog,
) -> None:
    """The stored kind selects its item and seeds the title and slug."""
    _view(dialog, wp_kind="podium")

    assert (
        _kind_label(dialog),
        _text(dialog, ids.WP_TITLE_INPUT),
        _text(dialog, ids.WP_SLUG_INPUT),
    ) == (
        _PODIUM_LABEL,
        default_title(_RIDE_NAME, "podium"),
        default_slug(_RIDE_NAME, "podium"),
    )


def test_publish_dialog_init_given_an_unknown_kind_shows_the_full_page_name(
    dialog: wx.Dialog,
) -> None:
    """A stored value this build rejects opens on the full kind."""
    _view(dialog, wp_kind="mystery")

    assert (
        _kind_label(dialog),
        _text(dialog, ids.WP_TITLE_INPUT),
        _text(dialog, ids.WP_SLUG_INPUT),
    ) == (
        _FULL_LABEL,
        default_title(_RIDE_NAME),
        default_slug(_RIDE_NAME),
    )


def test_publish_dialog_kind_change_given_the_full_item_reseeds_the_page_name(
    dialog: wx.Dialog,
) -> None:
    """Part C: moving the dropdown re-derives both fields."""
    view = _view(dialog, wp_kind="podium")
    view.kind_choice.SetSelection(0)

    view._on_kind_change(None)

    assert (
        _text(dialog, ids.WP_TITLE_INPUT),
        _text(dialog, ids.WP_SLUG_INPUT),
    ) == (default_title(_RIDE_NAME), default_slug(_RIDE_NAME))


def test_publish_dialog_collect_form_given_the_podium_item_collects_the_kind(
    dialog: wx.Dialog,
) -> None:
    """The collected form carries the dropdown's own kind."""
    view = _view(dialog, wp_kind="full")
    view.kind_choice.SetSelection(1)

    form = view.collect_form()

    assert form.kind == "podium"


# --------------------------------------------------------------------
# Phase 4b: the modal publish progress window (``publish_running_dlg``).
#
# ``PublishRunningDialog`` mirrors ``SimRunningDialog``: a code-side
# view over an XRC progress window that resolves the status line, the
# gauge and Cancel, points Escape at Cancel (a custom id, so wx's
# native escape path cannot find it) and defers its start into the
# modal loop ``run`` opens. The publish itself is a blocking HTTP
# call, so this view never aborts a request in flight: Cancel marks
# the attempt abandoned and the worker stops at its next checkpoint.

_STATUS_TEXT = "Publishing the results page…"


def _noop_start() -> None:
    """Do nothing: the view only forwards ``on_start``."""


def _running_view(
    dialog: wx.Dialog, *, on_start: Callable[[], None] = _noop_start
) -> PublishRunningDialog:
    """Decorate *dialog* as the publish progress view."""
    return PublishRunningDialog(dialog, on_start=on_start)


@pytest.fixture
def running_dialog(wx_app: Any) -> wx.Dialog:  # noqa: ANN401, ARG001 -- ordering only
    """Load the shipped ``publish_running_dlg`` for one test."""
    window: wx.Dialog = _support.fresh_resource().LoadDialog(None, ids.PUBLISH_RUNNING_DLG)
    _WINDOWS.append(window)
    return window


def test_fresh_resource_given_the_packaged_dir_resolves_the_publish_running_dialog(
    running_dialog: wx.Dialog,
) -> None:
    """The shipped progress window loads under its frozen name."""
    assert isinstance(running_dialog, wx.Dialog)
    assert running_dialog.GetName() == ids.PUBLISH_RUNNING_DLG


def test_publish_running_dialog_init_resolves_its_three_frozen_controls(
    running_dialog: wx.Dialog,
) -> None:
    """The status label, the gauge and Cancel all resolve by name."""
    view = _running_view(running_dialog)

    assert (
        view.publish_status_lbl.GetName(),
        view.progress_gauge.GetName(),
        view.cancel_btn.GetName(),
    ) == (ids.PUBLISH_STATUS_LBL, ids.PROGRESS_GAUGE, ids.CANCEL_BTN)


def test_publish_running_dialog_set_status_given_text_sets_the_label(
    running_dialog: wx.Dialog,
) -> None:
    """The live status line follows ``set_status``."""
    view = _running_view(running_dialog)

    view.set_status(_STATUS_TEXT)

    assert view.publish_status_lbl.GetLabel() == _STATUS_TEXT


def test_publish_running_dialog_cancel_click_given_the_button_sets_the_cancelled_flag(
    running_dialog: wx.Dialog,
) -> None:
    """Cancel marks the attempt abandoned; the worker polls the flag."""
    view = _running_view(running_dialog)
    event = wx.CommandEvent(wx.EVT_BUTTON.typeId, view.cancel_btn.GetId())

    view.cancel_btn.ProcessEvent(event)

    assert view.is_cancelled() is True


def test_publish_running_dialog_given_no_click_reports_not_cancelled(
    running_dialog: wx.Dialog,
) -> None:
    """T-3's other arm: a freshly opened dialog is not cancelled."""
    view = _running_view(running_dialog)

    assert view.is_cancelled() is False


def test_publish_running_dialog_escape_id_targets_the_cancel_button(
    running_dialog: wx.Dialog,
) -> None:
    """Escape lands on the same cancel path a click does."""
    view = _running_view(running_dialog)

    assert running_dialog.GetEscapeId() == view.cancel_btn.GetId()


class _FakeGauge:
    """A wxGauge double counting its pulses."""

    def __init__(self) -> None:
        """Start with no pulse recorded."""
        self.pulses = 0

    def Pulse(self) -> None:  # noqa: N802 -- wx API name
        """Record one pulse."""
        self.pulses += 1


class _FakeRunningDialog:
    """A wx.Dialog double recording its modal traffic."""

    def __init__(self) -> None:
        """Start unshown and unended."""
        self.shown = 0
        self.ended: list[int] = []

    def ShowModal(self) -> int:  # noqa: N802 -- wx API name
        """Record one modal open and return its result."""
        self.shown += 1
        return int(wx.ID_OK)

    def EndModal(self, result: int) -> None:  # noqa: N802 -- wx API name
        """Record one modal close."""
        self.ended.append(result)


def test_publish_running_dialog_pulse_given_the_gauge_pulses_and_yields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pulse animates the gauge and pumps the loop so it repaints."""
    shell = object.__new__(PublishRunningDialog)
    shell.progress_gauge = _FakeGauge()
    yields: list[int] = []
    monkeypatch.setattr(wx, "Yield", lambda: yields.append(1))

    shell.pulse()

    assert (shell.progress_gauge.pulses, yields) == (1, [1])


def test_publish_running_dialog_start_given_the_app_seam_calls_it() -> None:
    """``_start`` is the seam the app wires its worker start into."""
    shell = object.__new__(PublishRunningDialog)
    started: list[str] = []
    shell.on_start = lambda: started.append("worker")

    shell._start()

    assert started == ["worker"]


def test_publish_running_dialog_finish_ends_the_modal() -> None:
    """``finish`` is the app's seam for ending the modal."""
    shell = object.__new__(PublishRunningDialog)
    shell.dialog = _FakeRunningDialog()

    shell.finish()

    assert shell.dialog.ended == [int(wx.ID_OK)]


def test_publish_running_dialog_run_defers_start_then_shows_the_modal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_start`` runs inside the modal loop ``run`` opens."""
    shell = object.__new__(PublishRunningDialog)
    shell.dialog = _FakeRunningDialog()
    started: list[str] = []
    shell._start = lambda: started.append("started")
    deferred: list[Callable[[], None]] = []

    def _call_after(callable_: Callable[[], None]) -> None:
        deferred.append(callable_)
        callable_()

    monkeypatch.setattr(wx, "CallAfter", _call_after)

    shell.run()

    assert (started, deferred == [shell._start], shell.dialog.shown) == (["started"], True, 1)
