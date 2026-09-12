# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for ride_setup_dlg's staged logo (§3d).

``RideSetup.stage_logo`` is what ``logo_browse_btn`` runs once the
native picker returns a PNG: the picked file is resized into
:data:`~rivercrossing.ui.views.ride_setup.LOGO_STANDARD_SIZE` through
the pure ``team_editor.logo_fit_size`` rule and written to a temp file
-- the path ``_form_values`` submits and the store reads at
create/update time. ``show_logo`` renders a stored ride's own logo (D2
preload), or blanks the preview back to "NO LOGO".

Decoding and saving a PNG needs a live ``wx.App``, so this module
builds one -- the module-cache strong reference
``tests/unit/ui/test_main_frame_ride_header.py`` uses, because an
unbound ``wx.App()`` is collected as soon as its fixture goes out of
scope and the interpreter then hangs at exit. No window is ever
created, so the unit process never takes over a desktop.
"""

from __future__ import annotations

from functools import cache
from typing import TYPE_CHECKING, Any

import pytest
import wx
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui.views import ride_setup
from rivercrossing.ui.views.ride_setup import (
    LOGO_PREVIEW_SIZE,
    LOGO_STANDARD_SIZE,
    RideSetup,
    _fitted_image,
)

if TYPE_CHECKING:
    from pathlib import Path

# A 600x300 source fits the 256x256 box as 256x128 (2:1 preserved).
WIDE_PNG = (600, 300)
WIDE_FITTED = (256, 128)

# A source already inside the box is never upscaled (§3d).
SMALL_PNG = (100, 50)


@cache
def _app() -> wx.App:
    """Return this module's one ``wx.App``, creating it on first use.

    The log target is redirected to stderr -- the same guard
    ``ui.app`` installs and the functional conftest applies
    session-wide: a failed decode queues a wx error, and with no target
    to flush it ``wxApp::CleanUp()`` blocks at interpreter exit on a
    "Several errors occurred" modal nobody can dismiss (measured).
    """
    app = wx.GetApp() or wx.App(redirect=False)
    wx.Log.SetActiveTarget(wx.LogStderr())
    return app


@pytest.fixture(scope="module", autouse=True)
def _wx_app() -> wx.App:
    """Guarantee a live app before any wx object is built."""
    return _app()


class _RecordingBitmap:
    """``logo_preview_bmp`` double: records the bitmap rendered."""

    def __init__(self) -> None:
        """Start blank, as the authored XRC control does."""
        self.bitmap: Any = wx.NullBitmap

    def SetBitmap(self, bitmap: Any) -> None:  # noqa: N802, ANN401 -- wx's own Any
        """Record the bitmap the view rendered."""
        self.bitmap = bitmap


class _RecordingLabel:
    """``logo_status_lbl`` double: records the label rendered."""

    def __init__(self) -> None:
        """Start on the XRC's own default label."""
        self.label = "NO LOGO"

    def SetLabel(self, label: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the label the view rendered."""
        self.label = label


class _RecordingDialog:
    """The dialog double the view's ``Layout()`` calls land on."""

    def Layout(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Accept the layout request."""


class _StubEvent:
    """A wx command-event double recording ``Skip()``."""

    def __init__(self) -> None:
        """Start un-skipped."""
        self.skipped = False

    def Skip(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record that the handler forwarded the event."""
        self.skipped = True


def _bare_view(*, logo_path: Path | None = None) -> RideSetup:
    """Return a ``RideSetup`` over a staged-logo double set.

    ``__init__`` resolves every frozen name and builds a real
    ``wx.InfoBar``, which needs a desktop; the logo steps read/write
    only their own three attributes, so the instance is made without
    it (``test_team_editor_dialog_size.py``'s own stand-in shape).
    """
    view = object.__new__(RideSetup)
    view.dialog = _RecordingDialog()
    view._logo_path = logo_path
    view.logo_preview_bmp = _RecordingBitmap()
    view.logo_status_lbl = _RecordingLabel()
    return view


def _write_png(path: Path, size: tuple[int, int]) -> Path:
    """Write a solid-colour *size* PNG to *path*; return *path*."""
    image = wx.Image(size[0], size[1])
    image.SetRGB(wx.Rect(0, 0, size[0], size[1]), 12, 64, 200)
    image.SaveFile(str(path), wx.BITMAP_TYPE_PNG)
    return path


# --------------------------------------------- browsing: stage_logo


def test_ride_setup_stage_logo_given_an_oversized_png_writes_it_fitted(
    tmp_path: Path,
) -> None:
    """§3d: the staged copy fits LOGO_STANDARD_SIZE, aspect kept."""
    source = _write_png(tmp_path / "gorba.png", WIDE_PNG)
    view = _bare_view()

    view.stage_logo(source)

    staged = wx.Image(str(view._logo_path), wx.BITMAP_TYPE_PNG)
    assert (staged.GetWidth(), staged.GetHeight()) == WIDE_FITTED


def test_ride_setup_stage_logo_given_an_oversized_png_stages_a_copy(
    tmp_path: Path,
) -> None:
    """The operator's own file is never written to: the copy is new."""
    source = _write_png(tmp_path / "gorba.png", WIDE_PNG)
    view = _bare_view()

    view.stage_logo(source)

    assert view._logo_path.name == "gorba.png"
    assert view._logo_path != source


def test_ride_setup_stage_logo_given_a_png_within_the_box_keeps_its_size(
    tmp_path: Path,
) -> None:
    """§3d: a small logo is never upscaled into the standard box."""
    source = _write_png(tmp_path / "small.png", SMALL_PNG)
    view = _bare_view()

    view.stage_logo(source)

    staged = wx.Image(str(view._logo_path), wx.BITMAP_TYPE_PNG)
    assert (staged.GetWidth(), staged.GetHeight()) == SMALL_PNG


def test_ride_setup_stage_logo_given_a_png_previews_it_fitted(
    tmp_path: Path,
) -> None:
    """§3d: the preview renders inside LOGO_PREVIEW_SIZE."""
    source = _write_png(tmp_path / "gorba.png", WIDE_PNG)
    view = _bare_view()

    view.stage_logo(source)

    assert (
        view.logo_preview_bmp.bitmap.GetWidth(),
        view.logo_preview_bmp.bitmap.GetHeight(),
    ) == (LOGO_PREVIEW_SIZE[0], LOGO_PREVIEW_SIZE[0] // 2)


def test_ride_setup_stage_logo_given_a_png_shows_its_file_name(
    tmp_path: Path,
) -> None:
    """§3d: the status label names the staged file."""
    source = _write_png(tmp_path / "gorba.png", WIDE_PNG)
    view = _bare_view()

    view.stage_logo(source)

    assert view.logo_status_lbl.label == "gorba.png"


def test_ride_setup_stage_logo_given_an_undecodable_file_shows_no_logo(
    tmp_path: Path,
) -> None:
    """A file wx cannot decode stages nothing: "NO LOGO"."""
    broken = tmp_path / "broken.png"
    broken.write_bytes(b"not a png")
    view = _bare_view()

    view.stage_logo(broken)

    assert (view._logo_path, view.logo_status_lbl.label) == (None, "NO LOGO")
    assert not view.logo_preview_bmp.bitmap.IsOk()


def test_ride_setup_browse_given_a_picked_png_stages_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3d: logo_browse_btn stages whatever the picker returned."""
    source = _write_png(tmp_path / "gorba.png", WIDE_PNG)
    monkeypatch.setattr(ride_setup, "_pick_logo_path", lambda _parent: source)
    view = _bare_view()

    view._on_browse_logo(_StubEvent())

    staged = wx.Image(str(view._logo_path), wx.BITMAP_TYPE_PNG)
    assert (staged.GetWidth(), staged.GetHeight()) == WIDE_FITTED


def test_ride_setup_browse_given_a_picked_png_keeps_the_preview_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3d: one click leaves the three logo surfaces agreeing."""
    source = _write_png(tmp_path / "gorba.png", WIDE_PNG)
    monkeypatch.setattr(ride_setup, "_pick_logo_path", lambda _parent: source)
    view = _bare_view()

    view._on_browse_logo(_StubEvent())

    assert (
        view.logo_status_lbl.label,
        view.logo_preview_bmp.bitmap.GetWidth(),
        view._logo_path.name,
    ) == ("gorba.png", LOGO_PREVIEW_SIZE[0], "gorba.png")


def test_ride_setup_browse_given_a_cancelled_picker_stages_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """§3d: a cancelled picker leaves the "NO LOGO" default up."""
    monkeypatch.setattr(ride_setup, "_pick_logo_path", lambda _parent: None)
    view = _bare_view()

    view._on_browse_logo(_StubEvent())

    assert (view._logo_path, view.logo_status_lbl.label) == (None, "NO LOGO")


def test_ride_setup_browse_given_a_click_forwards_the_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The handler Skip()s, so wx's own command routing still runs."""
    monkeypatch.setattr(ride_setup, "_pick_logo_path", lambda _parent: None)
    view = _bare_view()
    event = _StubEvent()

    view._on_browse_logo(event)

    assert event.skipped is True


# ------------------------------------------- stored logos (show_logo)


def test_ride_setup_show_logo_given_a_stored_png_previews_it_fitted(
    tmp_path: Path,
) -> None:
    """D2 preload: the record's own PNG renders into the preview box."""
    stored = _write_png(tmp_path / "stored.png", WIDE_PNG)
    view = _bare_view()

    view.show_logo(stored)

    assert (
        view.logo_preview_bmp.bitmap.GetWidth(),
        view.logo_preview_bmp.bitmap.GetHeight(),
    ) == (LOGO_PREVIEW_SIZE[0], LOGO_PREVIEW_SIZE[0] // 2)


def test_ride_setup_show_logo_given_a_stored_png_clears_the_status_label(
    tmp_path: Path,
) -> None:
    """§3d: a rendered logo leaves the status label empty."""
    stored = _write_png(tmp_path / "stored.png", WIDE_PNG)
    view = _bare_view()

    view.show_logo(stored)

    assert (view._logo_path, view.logo_status_lbl.label) == (stored, "")


def test_ride_setup_show_logo_given_a_missing_file_falls_back_to_no_logo(
    tmp_path: Path,
) -> None:
    """A stale stored path reads as no logo, never as a blank dialog."""
    view = _bare_view()

    view.show_logo(tmp_path / "gone.png")

    assert (view._logo_path, view.logo_status_lbl.label) == (None, "NO LOGO")
    assert not view.logo_preview_bmp.bitmap.IsOk()


# ---------------------------------------------- the fit rule: T-7/T-4

# T-4 boundary rows for the 256x256 box: the smallest image, a source
# well inside it, exactly at the box, one pixel past it (each side,
# rounded), and a 1-pixel-tall source (the rounding floor).
_FIT_CASES: list[tuple[tuple[int, int], tuple[int, int]]] = [
    ((1, 1), (1, 1)),
    ((100, 50), (100, 50)),
    ((256, 256), (256, 256)),
    ((257, 256), (256, 255)),
    ((600, 300), WIDE_FITTED),
    ((1000, 1), (256, 1)),
]


@pytest.mark.parametrize(("size", "expected"), _FIT_CASES)
def test_ride_setup_fitted_image_given_a_box_boundary_returns_the_fitted_size(
    size: tuple[int, int], expected: tuple[int, int]
) -> None:
    """T-4: the box boundaries fit exactly as the pure rule says."""
    image = wx.Image(size[0], size[1])

    fitted = _fitted_image(image, within=LOGO_STANDARD_SIZE)

    assert (fitted.GetWidth(), fitted.GetHeight()) == expected


@given(
    width=st.integers(min_value=1, max_value=600),
    height=st.integers(min_value=1, max_value=600),
)
def test_ride_setup_fitted_image_given_any_size_stays_inside_the_box(
    width: int, height: int
) -> None:
    """T-7 invariant: the fitted image stays inside the box."""
    image = wx.Image(width, height)

    fitted = _fitted_image(image, within=LOGO_STANDARD_SIZE)

    assert fitted.GetWidth() <= LOGO_STANDARD_SIZE[0]
    assert fitted.GetHeight() <= LOGO_STANDARD_SIZE[1]


@given(
    width=st.integers(min_value=1, max_value=600),
    height=st.integers(min_value=1, max_value=600),
)
def test_ride_setup_fitted_image_given_any_size_keeps_the_aspect_ratio(
    width: int, height: int
) -> None:
    """T-7 invariant: the fit distorts by at most one rounding pixel."""
    image = wx.Image(width, height)

    fitted = _fitted_image(image, within=LOGO_STANDARD_SIZE)

    assert abs(fitted.GetWidth() * height - fitted.GetHeight() * width) <= max(width, height)
