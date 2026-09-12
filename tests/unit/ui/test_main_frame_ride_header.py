# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the console's ride-info header (plan §5).

``show_ride_header`` renders the Ride group beside the stop light: the
ride's own logo plus six read-only values -- Name, Date, Venue,
Organizer, Scorer and Lap length km. Three things are pinned here:

- :func:`rivercrossing.ui.views.main_frame._ride_logo_bitmap` -- a path
  decodes to an OK bitmap fitted into ``RIDE_LOGO_DISPLAY_SIZE``, or the
  slot hides (``None`` for no file, an unreadable file, or bytes wx
  cannot decode).
- the two size constants, tied back to main.xrc's own authored sizes.
- ``show_ride_header``/``show_no_ride``'s writes to the six value
  controls, logo slot and status lamp, through recording doubles over a
  frame built with ``object.__new__``
  (``test_ride_setup_logo_wx.py``'s precedent): no wx window is ever
  created, so the unit process never takes over a desktop.

The bitmap arm needs a live ``wx.App`` to decode and rescale a PNG, so
this module builds one -- the module-cache strong reference
``test_cards_imagelist_wx.py`` uses, because an unbound ``wx.App()`` is
collected as soon as its fixture goes out of scope and the interpreter
then hangs at exit. The app also installs the same ``wx.LogStderr()``
target ``ui.app`` does: the missing- and undecodable-file cases make wx
queue log errors, and without a target to flush them the interpreter
hangs at exit on an undismissable "Several errors occurred" modal
(measured exit 124).
"""

import base64
from datetime import date, datetime
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import wx
from defusedxml.ElementTree import parse
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode
from rivercrossing.ui.views.main_frame import (
    RIDE_INFO_VALUE_WIDTH,
    RIDE_LOGO_DISPLAY_SIZE,
    MainFrame,
    _ride_logo_bitmap,
)

if TYPE_CHECKING:
    from collections.abc import Callable

# A canonical 1x1 transparent PNG (67 bytes) -- a real, decodable
# image, not a placeholder byte string.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
)

# The authored main.xrc, for the constants' drift pin (one source tree,
# so the code-side sizes and the declared ones are checked together).
_MAIN_XRC = (
    Path(__file__).resolve().parents[3] / "src" / "rivercrossing" / "ui" / "xrc" / "main.xrc"
)

# §5: the six read-only value rows, in the Ride box's own order.
_RIDE_INFO_VALUE_NAMES = (
    "ride_name_value",
    "ride_date_value",
    "ride_venue_value",
    "ride_organizer_value",
    "ride_scorer_value",
    "ride_lap_km_value",
)

# The canonical header call -- the GORBA ride ``tests/conftest.py``'s
# own config builds -- so each pin varies only the field it is about.
_HEADER_FIELDS: dict[str, object] = {
    "name": "GORBA EPIC 2026",
    "logo": None,
    "event_date": date(2026, 9, 20),
    "planned_start": datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive, by design
    "entry_mode": EntryMode.MIXED,
    "venue": "Sea to Sky Gondola",
    "organizer": "GORBA",
    "scorer": "K. Singh",
    "lap_km": 8.0,
}

# The render methods this pin does not exercise: the no-ride reset also
# blanks the clock, feed and counters, and none of those are asserted
# here (their own modules own them).
_SILENCED_METHODS = (
    "show_clock",
    "set_clock_fractions",
    "show_feed",
    "show_flagged",
    "show_riders",
    "show_counters",
)


@cache
def _app() -> wx.App:
    """Return this module's one ``wx.App``, creating it on first use.

    ``wx.Bitmap`` decoding needs a live app; the cache is a strong
    module-level reference so the app outlives every test (the
    measured unbound-app hang ``test_cards_imagelist_wx`` documents).

    The active log target is redirected to stderr -- the same guard
    ``ui.app`` installs and ``tests/functional/conftest.py`` applies
    session-wide. A missing or undecodable logo makes wx *queue* an
    error rather than print it, and with no target to flush the queue
    ``wxApp::CleanUp()`` blocks at interpreter exit on a "Several
    errors occurred" modal nobody can dismiss (measured exit 124).
    """
    app = wx.GetApp() or wx.App(redirect=False)
    wx.Log.SetActiveTarget(wx.LogStderr())
    return app


@pytest.fixture(scope="module", autouse=True)
def _wx_app() -> wx.App:
    """Guarantee a live app before any wx object is built."""
    return _app()


def _no_op(*_args: object, **_kwargs: object) -> None:
    """Accept any call the view makes on a double."""


class _RecordingValue:
    """``ride_*_value`` double: records the rendered value."""

    def __init__(self) -> None:
        """Start blank, as the authored XRC control does."""
        self.value = ""

    def SetValue(self, value: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the value the view rendered."""
        self.value = value


class _RecordingBitmap:
    """``ride_logo_bmp`` double: records the bitmap and visibility."""

    def __init__(self) -> None:
        """Start blank and shown, as the authored XRC control does."""
        self.bitmap: Any = wx.NullBitmap
        self.shown = True

    def SetBitmap(self, bitmap: Any) -> None:  # noqa: N802, ANN401 -- wx's own Any
        """Record the bitmap the view rendered."""
        self.bitmap = bitmap

    def Show(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record that the slot is visible."""
        self.shown = True

    def Hide(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record that the slot is hidden."""
        self.shown = False


class _RecordingLight:
    """``ride_status_light`` double: records the lamp mode."""

    def __init__(self) -> None:
        """Start dark, as a fresh console does."""
        self.mode = "off"

    def set_mode(self, mode: str) -> None:
        """Record the mode the view applied."""
        self.mode = mode


class _NoOp:
    """Any control the pin never asserts on: accepts every call."""

    def __getattr__(self, _name: str) -> Callable[..., None]:
        """Return a no-op for whatever wx member the view calls."""
        return _no_op


def _bare_view() -> MainFrame:
    """Return a ``MainFrame`` over recording doubles.

    ``__init__`` resolves every frozen name, builds real ``wx.InfoBar``s
    and takes over a desktop, so the instance is made without it
    (``test_ride_setup_logo_wx.py``'s own stand-in shape): the header
    steps read and write only these attributes.
    """
    view = object.__new__(MainFrame)
    view.frame = _NoOp()
    view._on_ride_changed = None
    view.ride_logo_bmp = _RecordingBitmap()
    view.ride_name_value = _RecordingValue()
    view.ride_date_value = _RecordingValue()
    view.ride_venue_value = _RecordingValue()
    view.ride_organizer_value = _RecordingValue()
    view.ride_scorer_value = _RecordingValue()
    view.ride_lap_km_value = _RecordingValue()
    view.ride_status_lbl = _NoOp()
    view.ride_status_light = _RecordingLight()
    for name in (
        "plate_input",
        "record_btn",
        "start_btn",
        "stop_btn",
        "undo_btn",
        "resume_infobar",
        "reopened_infobar",
        "finished_infobar",
    ):
        setattr(view, name, _NoOp())
    for name in _SILENCED_METHODS:
        setattr(view, name, _no_op)
    return view


def _render_header(view: MainFrame, **overrides: object) -> None:
    """Render the canonical header, with *overrides* applied.

    One place spells the nine-field call; each pin varies only the field
    it is about -- still one public-method call on the SUT (T-8).
    """
    view.show_ride_header(**{**_HEADER_FIELDS, **overrides})


def _write_png(path: Path, size: tuple[int, int]) -> Path:
    """Write a solid-colour *size* PNG to *path*; return *path*."""
    image = wx.Image(size[0], size[1])
    image.SetRGB(wx.Rect(0, 0, size[0], size[1]), 12, 64, 200)
    image.SaveFile(str(path), wx.BITMAP_TYPE_PNG)
    return path


def _authored_size(name: str) -> str:
    """Return the ``<size>`` main.xrc declares for the named control."""
    control = next(
        obj for obj in parse(_MAIN_XRC).getroot().iter("object") if obj.attrib.get("name") == name
    )
    size = control.find("size")
    return "" if size is None or size.text is None else size.text


def _authored_value_sizes() -> set[str]:
    """Return the ``<size>`` each of the six value controls declares."""
    return {_authored_size(name) for name in _RIDE_INFO_VALUE_NAMES}


# ------------------------------------------------- header size pins


def test_ride_logo_display_size_is_a_64_pixel_box() -> None:
    """§5: the logo slot's code-side size."""
    assert RIDE_LOGO_DISPLAY_SIZE == (64, 64)


def test_ride_info_value_width_is_240_pixels() -> None:
    """§5: each value row's code-side width."""
    assert RIDE_INFO_VALUE_WIDTH == 240


def test_ride_info_size_constants_mirror_the_authored_xrc_sizes() -> None:
    """main.xrc declares the same slot and row sizes the code does."""
    assert (_authored_size("ride_logo_bmp"), _authored_value_sizes()) == (
        f"{RIDE_LOGO_DISPLAY_SIZE[0]},{RIDE_LOGO_DISPLAY_SIZE[1]}",
        {f"{RIDE_INFO_VALUE_WIDTH},-1"},
    )


# --------------------------------------------------- value rendering


def test_show_ride_header_given_an_open_ride_renders_the_six_value_rows() -> None:
    """§5: name, date, venue, organizer, scorer and lap km render."""
    view = _bare_view()

    _render_header(view)

    assert (
        view.ride_name_value.value,
        view.ride_date_value.value,
        view.ride_venue_value.value,
        view.ride_organizer_value.value,
        view.ride_scorer_value.value,
        view.ride_lap_km_value.value,
    ) == ("GORBA EPIC 2026", "2026-09-20", "Sea to Sky Gondola", "GORBA", "K. Singh", "8.0")


@pytest.mark.parametrize(
    ("event_date", "expected"),
    [
        (date(2026, 9, 20), "2026-09-20"),
        (date(2026, 1, 2), "2026-01-02"),
        (date(2026, 12, 31), "2026-12-31"),
    ],
    ids=["plain", "zero_padded_month_and_day", "year_end"],
)
def test_show_ride_header_given_a_date_renders_its_iso_form(
    event_date: date, expected: str
) -> None:
    """§5: the Date row is ``event_date.isoformat()``."""
    view = _bare_view()

    _render_header(view, event_date=event_date)

    assert view.ride_date_value.value == expected


@pytest.mark.parametrize(
    ("lap_km", "expected"),
    [
        (0.1, "0.1"),
        (1.0, "1.0"),
        (8.0, "8.0"),
        (42.195, "42.195"),
    ],
    ids=["fraction", "one_km", "gorba", "marathon"],
)
def test_show_ride_header_given_a_lap_length_renders_it_as_text(
    lap_km: float, expected: str
) -> None:
    """§5: the Lap length km row is ``str(lap_km)``."""
    view = _bare_view()

    _render_header(view, lap_km=lap_km)

    assert view.ride_lap_km_value.value == expected


def test_show_ride_header_given_blank_text_fields_renders_empty_values() -> None:
    """T-4 present-but-empty: a blank setup renders an empty value."""
    view = _bare_view()

    _render_header(view, name="", venue="", organizer="", scorer="")

    assert (
        view.ride_name_value.value,
        view.ride_venue_value.value,
        view.ride_organizer_value.value,
        view.ride_scorer_value.value,
    ) == ("", "", "", "")


def test_show_ride_header_given_no_logo_hides_the_logo_slot() -> None:
    """C1/§5: a ride with no logo blanks the slot, not a stale one."""
    view = _bare_view()

    _render_header(view, logo=None)

    assert view.ride_logo_bmp.shown is False


def test_show_ride_header_given_a_png_logo_renders_it_fitted(tmp_path: Path) -> None:
    """§5: the logo renders inside the 64x64 slot, aspect preserved."""
    logo = _write_png(tmp_path / "gorba.png", (600, 300))
    view = _bare_view()

    _render_header(view, logo=logo)

    assert (
        view.ride_logo_bmp.bitmap.GetWidth(),
        view.ride_logo_bmp.bitmap.GetHeight(),
    ) == (64, 32)


def test_show_ride_header_given_a_png_logo_shows_the_logo_slot(tmp_path: Path) -> None:
    """§5: a rendered logo is shown again after a no-ride blank."""
    logo = _write_png(tmp_path / "gorba.png", (600, 300))
    view = _bare_view()
    view.ride_logo_bmp.Hide()

    _render_header(view, logo=logo)

    assert view.ride_logo_bmp.shown is True


# ---------------------------------------------------- no-ride blank


def test_show_no_ride_given_a_rendered_header_clears_the_six_values() -> None:
    """W1/§5: the no-ride console blanks every value row."""
    view = _bare_view()
    _render_header(view)

    view.show_no_ride()

    assert (
        view.ride_name_value.value,
        view.ride_date_value.value,
        view.ride_venue_value.value,
        view.ride_organizer_value.value,
        view.ride_scorer_value.value,
        view.ride_lap_km_value.value,
    ) == ("", "", "", "", "", "")


def test_show_no_ride_given_a_rendered_header_hides_the_logo_slot(tmp_path: Path) -> None:
    """W1/§5: no ride means no logo in the slot."""
    logo = _write_png(tmp_path / "gorba.png", (600, 300))
    view = _bare_view()
    _render_header(view, logo=logo)

    view.show_no_ride()

    assert view.ride_logo_bmp.shown is False


def test_show_no_ride_given_a_live_header_puts_the_status_lamp_off() -> None:
    """W1: the status column's lamp goes dark with no ride."""
    view = _bare_view()
    view.ride_status_light.set_mode("green")

    view.show_no_ride()

    assert view.ride_status_light.mode == "off"


def test_show_no_ride_given_a_live_header_returns_the_console_to_draft() -> None:
    """W1: the ride-state seam sees DRAFT after a clear."""
    view = _bare_view()
    view._status = RideStatus.RUNNING

    view.show_no_ride()

    assert view._status is RideStatus.DRAFT


# ------------------------------------------------------- logo bitmap


def test_ride_logo_bitmap_given_no_path_returns_none() -> None:
    """No logo chosen: the slot hides."""
    result = _ride_logo_bitmap(None)

    assert result is None


def test_ride_logo_bitmap_given_a_missing_file_returns_none(tmp_path: Path) -> None:
    """T-5 negative: a path with no file hides the slot."""
    result = _ride_logo_bitmap(tmp_path / "not-there.png")

    assert result is None


def test_ride_logo_bitmap_given_undecodable_bytes_returns_none(tmp_path: Path) -> None:
    """T-5 negative: bytes wx cannot decode never blank the header."""
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not a png at all")

    result = _ride_logo_bitmap(corrupt)

    assert result is None


def test_ride_logo_bitmap_given_a_png_file_returns_a_valid_bitmap(tmp_path: Path) -> None:
    """A real PNG decodes into the bitmap the header renders."""
    logo = tmp_path / "logo.png"
    logo.write_bytes(_TINY_PNG)

    result = _ride_logo_bitmap(logo)

    assert result.IsOk() is True
    assert (result.GetWidth(), result.GetHeight()) == (1, 1)


def test_ride_logo_bitmap_given_an_oversized_png_fits_it_into_the_display_box(
    tmp_path: Path,
) -> None:
    """§5: a wide logo renders at the slot's own 64x64 box."""
    logo = _write_png(tmp_path / "wide.png", (600, 300))

    result = _ride_logo_bitmap(logo)

    assert (result.GetWidth(), result.GetHeight()) == (64, 32)


def test_ride_logo_bitmap_given_a_png_within_the_box_keeps_its_size(tmp_path: Path) -> None:
    """§5: a small logo is never upscaled into the slot."""
    logo = _write_png(tmp_path / "small.png", (32, 16))

    result = _ride_logo_bitmap(logo)

    assert (result.GetWidth(), result.GetHeight()) == (32, 16)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    width=st.integers(min_value=1, max_value=200),
    height=st.integers(min_value=1, max_value=200),
)
def test_ride_logo_bitmap_given_any_png_stays_inside_the_display_box(
    tmp_path: Path, width: int, height: int
) -> None:
    """T-7 invariant: a fitted logo never exceeds the display box.

    The suppressed health check is safe here: each generated example
    overwrites the same ``tmp_path`` PNG before decoding it, so no state
    leaks between inputs.
    """
    logo = _write_png(tmp_path / "logo.png", (width, height))

    result = _ride_logo_bitmap(logo)

    assert result.GetWidth() <= RIDE_LOGO_DISPLAY_SIZE[0]
    assert result.GetHeight() <= RIDE_LOGO_DISPLAY_SIZE[1]
