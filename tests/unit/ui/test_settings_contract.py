# SPDX-License-Identifier: GPL-3.0-only
"""Headless contract pins for ``SettingsDialog`` (settings_dlg, 3a).

Constructing a real ``wx.Dialog`` needs a desktop, so these tests
drive the view over control doubles with ``find_control`` patched at
the module seam -- the same double-driven shape
``test_ride_setup_contract.py`` uses for ``ride_setup_dlg``. Phase 1
re-shaped two of the dialog's surfaces, pinned here:

* the average rider speed is a one-decimal ``wxSpinCtrlDouble``
  (``avg_speed_spin``) whose value now round-trips the decimal the
  old integer spin truncated (``12.0``, never ``12``);
* the "Back up now" button -- and the ``on_backup_now`` constructor
  seam behind it -- is gone (File ▸ Back Up Database… keeps the
  R-54 manual backup).
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, Any

import pytest
import wx

from rivercrossing.ui import ids
from rivercrossing.ui.presenters.settings import AppSettings, default_settings
from rivercrossing.ui.views import settings as settings_view
from rivercrossing.ui.views.settings import SettingsDialog

if TYPE_CHECKING:
    from collections.abc import Callable

# The dialog's appearance trio, as the canvas names them.
APPEARANCE_RADIOS = (
    ids.APPEARANCE_SYSTEM_RADIO,
    ids.APPEARANCE_LIGHT_RADIO,
    ids.APPEARANCE_DARK_RADIO,
)


class _FakeControl:
    """A child-control double: the getters/setters the view calls."""

    def __init__(self, value: object) -> None:
        """Start on *value*."""
        self._value = value

    def GetValue(self) -> object:  # noqa: N802 -- wx API name the SUT calls
        """Return the value this double was built with."""
        return self._value

    def SetValue(self, value: object) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the value the view rendered."""
        self._value = value


class _FakeDialog:
    """The dialog double the view's own dialog calls land on."""

    def __init__(self) -> None:
        """Start with no bindings and no modal result."""
        self.bindings: list[dict[str, Any]] = []
        self.modal_results: list[int] = []

    def Bind(self, event_type: object, handler: Any, **kwargs: Any) -> None:  # noqa: ANN401, N802 -- wx API name
        """Record a binding (wx's own keyword ``id`` included)."""
        self.bindings.append({"event_type": event_type, "handler": handler, **kwargs})

    def EndModal(self, result: int) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the modal result."""
        self.modal_results.append(result)


def _controls() -> dict[str, _FakeControl]:
    """Return one control double per frozen name the view resolves."""
    return {
        ids.APPEARANCE_SYSTEM_RADIO: _FakeControl(value=False),
        ids.APPEARANCE_LIGHT_RADIO: _FakeControl(value=False),
        ids.APPEARANCE_DARK_RADIO: _FakeControl(value=False),
        ids.SOUND_CHK: _FakeControl(value=False),
        ids.HIDE_TIMES_CHK: _FakeControl(value=False),
        ids.VERBOSE_LOG_CHK: _FakeControl(value=False),
        ids.AVG_SPEED_SPIN: _FakeControl(value=12.0),
    }


def _stub_find_control(controls: dict[str, _FakeControl]) -> Callable[..., _FakeControl]:
    """Return a ``find_control`` stub answering from *controls*."""

    def find_control(_window: object, name: str, _kind: object = None) -> _FakeControl:
        """Return the double registered under *name*."""
        return controls[name]

    return find_control


def _build_view(
    monkeypatch: pytest.MonkeyPatch,
    *,
    settings: AppSettings,
    on_save: Callable[[AppSettings], None] | None = None,
) -> tuple[SettingsDialog, dict[str, _FakeControl], _FakeDialog]:
    """Decorate a dialog double, with ``find_control`` stubbed."""
    controls = _controls()
    dialog = _FakeDialog()
    monkeypatch.setattr(settings_view, "find_control", _stub_find_control(controls))
    view = SettingsDialog(
        dialog,  # type: ignore[arg-type] -- the double stands in for the loaded dialog
        settings=settings,
        on_save=on_save if on_save is not None else (lambda _collected: None),
    )
    return view, controls, dialog


# ------------------------------------- the decimal average-speed entry


def test_settings_dialog_init_given_a_fractional_speed_shows_the_decimal_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The old integer spin rounded 12.5 to 12; the entry must not."""
    settings = replace(default_settings(), avg_speed_kmh=12.5)

    _view, controls, _dialog = _build_view(monkeypatch, settings=settings)

    assert controls[ids.AVG_SPEED_SPIN].GetValue() == 12.5


def test_settings_dialog_init_given_a_whole_speed_shows_a_float_not_an_int(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """12.0 renders as the float 12.0: the decimal digit survives."""
    settings = replace(default_settings(), avg_speed_kmh=12.0)

    _view, controls, _dialog = _build_view(monkeypatch, settings=settings)

    shown = controls[ids.AVG_SPEED_SPIN].GetValue()
    assert (shown, isinstance(shown, float)) == (12.0, True)


def test_settings_dialog_collect_settings_given_a_decimal_entry_returns_the_float(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OK collects the entry's own float, not a rounded integer."""
    view, controls, _dialog = _build_view(monkeypatch, settings=default_settings())
    controls[ids.AVG_SPEED_SPIN].SetValue(17.5)

    collected = view.collect_settings()

    assert (collected.avg_speed_kmh, isinstance(collected.avg_speed_kmh, float)) == (17.5, True)


def test_settings_dialog_collect_settings_carries_the_dialogless_fields_through(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Zoom and the two layout fields have no control (W13/E8.1.1)."""
    settings = replace(
        default_settings(),
        zoom_percent=130,
        splitter_sash=320,
        window_geometry=(10, 20, 30, 40),
    )
    view, _controls, _dialog = _build_view(monkeypatch, settings=settings)

    collected = view.collect_settings()

    assert (collected.zoom_percent, collected.splitter_sash, collected.window_geometry) == (
        130,
        320,
        (10, 20, 30, 40),
    )


# ---------------------------------------- the appearance radio render


@pytest.mark.parametrize("appearance", ["system", "light", "dark"])
def test_settings_dialog_init_given_an_appearance_checks_exactly_its_radio(
    monkeypatch: pytest.MonkeyPatch, appearance: str
) -> None:
    """One radio reads checked; the other two read unchecked."""
    settings = replace(default_settings(), appearance=appearance)

    _view, controls, _dialog = _build_view(monkeypatch, settings=settings)

    checked = tuple(name for name in APPEARANCE_RADIOS if controls[name].GetValue())
    assert checked == (f"appearance_{appearance}_radio",)


# ------------------------------------------- the retired backup seam


def test_settings_dialog_init_given_a_backup_seam_raises_type_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Phase 1 removed the seam from the constructor."""
    monkeypatch.setattr(settings_view, "find_control", _stub_find_control(_controls()))

    with pytest.raises(TypeError, match="unexpected keyword argument 'on_backup_now'"):
        SettingsDialog(
            _FakeDialog(),  # type: ignore[arg-type] -- the double stands in for the loaded dialog
            settings=default_settings(),
            on_save=lambda _collected: None,
            on_backup_now=lambda: None,  # type: ignore[call-arg] -- the retired seam under test
        )


def test_settings_dialog_init_binds_only_the_ok_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The dialog's one binding is OK (no backup seam left)."""
    _view, _controls, dialog = _build_view(monkeypatch, settings=default_settings())

    assert [binding["id"] for binding in dialog.bindings] == [wx.ID_OK]


# ------------------------------------------------------------ the OK


def test_settings_dialog_ok_given_the_form_saves_the_collected_settings_and_closes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OK collects, saves the fresh settings, then ends modal."""
    saved: list[AppSettings] = []
    view, controls, dialog = _build_view(
        monkeypatch, settings=default_settings(), on_save=saved.append
    )
    controls[ids.AVG_SPEED_SPIN].SetValue(21.5)

    view._on_ok(None)

    assert (len(saved), saved[0].avg_speed_kmh, dialog.modal_results) == (1, 21.5, [wx.ID_OK])
