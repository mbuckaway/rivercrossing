# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for the console's ride-identity header (C1).

``show_ride_header`` replaces the one-line ``show_ride_name``: the
console renders the ride's name, its logo when one is present, and
otherwise a date / start / type detail line. The two decisions that
sit under it are pure and are pinned here:

- :func:`rivercrossing.ui.views.main_frame._ride_details_label` --
  the date/start/type text, no wx object at all.
- :func:`rivercrossing.ui.views.main_frame._ride_logo_bitmap` -- a
  path decodes to an OK bitmap, or the header falls back to the
  detail line (``None`` for no file, an unreadable file, or bytes wx
  cannot decode).

The bitmap arm needs a live ``wx.App`` to decode a PNG, so this
module builds one locally -- the module-cache strong reference
``tests/unit/ui/test_cards_imagelist_wx.py`` uses, because an unbound
``wx.App()`` is collected as soon as its fixture goes out of scope
and the interpreter then hangs at exit. The app also installs the
same ``wx.LogStderr()`` target ``ui.app`` and ``tests/functional/
conftest.py`` do: the missing- and undecodable-file cases make wx
queue log errors, and without a target to flush them the interpreter
hangs at exit on an undismissable "Several errors occurred" modal
(measured exit 124). No window is ever created, so the unit process
never takes over a desktop; the real frame's rendering stays in the
functional suite.
"""

import base64
from datetime import date, datetime
from functools import cache
from typing import TYPE_CHECKING

import pytest
import wx

from rivercrossing.roster import EntryMode
from rivercrossing.ui.views.main_frame import _ride_details_label, _ride_logo_bitmap

if TYPE_CHECKING:
    from pathlib import Path

# A canonical 1x1 transparent PNG (67 bytes) -- a real, decodable
# image, not a placeholder byte string.
_TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQ"
    "AAAABJRU5ErkJggg=="
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


# ----------------------------------------------------- detail line


@pytest.mark.parametrize(
    ("event_date", "planned_start", "entry_mode", "expected"),
    [
        (
            date(2026, 9, 20),
            datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive, by design
            EntryMode.MIXED,
            "2026-09-20 · 10:00 · Mixed",
        ),
        (
            date(2026, 1, 2),
            datetime(2026, 1, 2, 9, 5),  # noqa: DTZ001 -- naive, by design
            EntryMode.SOLO,
            "2026-01-02 · 09:05 · Solo",
        ),
        (
            date(2026, 12, 31),
            datetime(2026, 12, 31, 0, 0),  # noqa: DTZ001 -- naive, by design
            EntryMode.MIXED,
            "2026-12-31 · 00:00 · Mixed",
        ),
    ],
    ids=["mixed", "solo_padded", "midnight"],
)
def test_ride_details_label_renders_date_start_and_type(  # noqa: PLR0913, PLR0917 -- the table's four columns
    event_date: date, planned_start: datetime, entry_mode: EntryMode, expected: str
) -> None:
    """C1: the detail line is ISO date, HH:MM start, ride type."""
    result = _ride_details_label(event_date, planned_start, entry_mode)

    assert result == expected


# ------------------------------------------------------- logo bitmap


def test_ride_logo_bitmap_given_no_path_returns_none() -> None:
    """No logo chosen: the header keeps the detail line."""
    _app()

    result = _ride_logo_bitmap(None)

    assert result is None


def test_ride_logo_bitmap_given_a_missing_file_returns_none(tmp_path: Path) -> None:
    """T-5 negative: a path with no file degrades to the detail line."""
    _app()

    result = _ride_logo_bitmap(tmp_path / "not-there.png")

    assert result is None


def test_ride_logo_bitmap_given_undecodable_bytes_returns_none(tmp_path: Path) -> None:
    """T-5 negative: bytes wx cannot decode never blank the header."""
    _app()
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not a png at all")

    result = _ride_logo_bitmap(corrupt)

    assert result is None


def test_ride_logo_bitmap_given_a_png_file_returns_a_valid_bitmap(tmp_path: Path) -> None:
    """A real PNG decodes into the bitmap the header renders."""
    _app()
    logo = tmp_path / "logo.png"
    logo.write_bytes(_TINY_PNG)

    result = _ride_logo_bitmap(logo)

    assert result is not None
    assert result.IsOk() is True
