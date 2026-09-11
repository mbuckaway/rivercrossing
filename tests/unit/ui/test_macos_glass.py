# SPDX-License-Identifier: GPL-3.0-only
"""Guard and dispatch pins for the macOS-26 ``.glass`` bezel (W10).

``_support.apply_glass_bezel`` reaches the native ``NSButton`` behind
a ``wx.Button`` and sends ``setBezelStyle:`` with
``NSBezelStyleGlass`` (16, macOS 26+). The message itself is a real
Objective-C call on a real native view, so it cannot be sent in CI --
only the decisions around it are pinned here:

* the platform/version guard (off on Windows, Linux, and every macOS
  before 26; on for 26 and later);
* the not-yet-realized guard (``GetHandle()`` returns 0 before the
  dialog is shown);
* the dispatch itself, through a faked ``_send_set_bezel_style`` and a
  button stub whose ``GetHandle()`` is a fixed int.

The one arm deliberately left to the user's manual macOS-26 check is
the ctypes send inside ``_send_set_bezel_style``: dereferencing a fake
pointer would abort the interpreter (the wx C++ assertion hazard
``AGENTS.md`` records), which is exactly why the function takes the
handle as a plain int and is exercised only through its no-libobjc
guard here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from rivercrossing.ui.views import _support
from rivercrossing.ui.views._support import GLASS_BEZEL_STYLE, apply_glass_bezel

if TYPE_CHECKING:
    import wx


class _FakeButton:
    """A ``wx.Button`` stand-in whose native handle is a fixed int."""

    def __init__(self, handle: int) -> None:
        """Fix the pointer this stub reports as its native handle."""
        self._handle = handle

    def GetHandle(self) -> int:  # noqa: N802 -- the wx method this stub stands in for
        """Return the fixed handle (0 = not yet realized)."""
        return self._handle


def _faked_sends(monkeypatch: pytest.MonkeyPatch) -> list[tuple[object, int]]:
    """Record every ``_send_set_bezel_style`` dispatch in the test."""
    sends: list[tuple[object, int]] = []

    def _record(handle: object, style: int) -> None:
        sends.append((handle, style))

    monkeypatch.setattr(_support, "_send_set_bezel_style", _record)
    return sends


def _button(handle: int) -> wx.Button:
    """Wrap *handle* in a stub typed as the ``wx.Button`` seam."""
    return cast("wx.Button", _FakeButton(handle))


# --- the enum value the ctypes send pins -----------------------------


def test_glass_bezel_style_is_the_macos_26_nsbezelstyle_glass_value() -> None:
    """``NSBezelStyleGlass`` is 16 on macOS 26.0+."""
    assert GLASS_BEZEL_STYLE == 16


# --- _glass_bezel_supported ------------------------------------------


def test_glass_bezel_supported_off_macos_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows and Linux never take the native-handle path."""
    monkeypatch.setattr(_support.sys, "platform", "win32")

    assert _support._glass_bezel_supported() is False


@pytest.mark.parametrize("major", [15, 25])  # T-4: below the floor
def test_glass_bezel_supported_below_macos_26_is_false(
    monkeypatch: pytest.MonkeyPatch, major: int
) -> None:
    """Every macOS before 26 has no ``NSBezelStyleGlass`` to set."""
    monkeypatch.setattr(_support.sys, "platform", "darwin")
    monkeypatch.setattr(_support, "_macos_major", lambda: major)

    assert _support._glass_bezel_supported() is False


@pytest.mark.parametrize("major", [26, 27])  # T-4: the floor and above
def test_glass_bezel_supported_on_macos_26_or_later_is_true(
    monkeypatch: pytest.MonkeyPatch, major: int
) -> None:
    """Tahoe (macOS 26) introduced the glass material; 27 keeps it."""
    monkeypatch.setattr(_support.sys, "platform", "darwin")
    monkeypatch.setattr(_support, "_macos_major", lambda: major)

    assert _support._glass_bezel_supported() is True


# --- _macos_major ----------------------------------------------------


@pytest.mark.parametrize(
    ("version", "expected"),
    [
        ("26.6.2", 26),
        ("26.0", 26),
        ("26", 26),  # T-4: no minor/patch at all
        ("15.6.1", 15),
        ("", 0),  # T-4: missing version reads as "unknown", never 26
        ("not-a-version", 0),
    ],
)
def test_macos_major_given_a_version_string_returns_its_leading_integer(
    monkeypatch: pytest.MonkeyPatch, version: str, expected: int
) -> None:
    """The guard parses ``platform.mac_ver()``'s leading component."""
    monkeypatch.setattr(_support.platform, "mac_ver", lambda: (version, ("", "", ""), ""))

    assert _support._macos_major() == expected


# --- apply_glass_bezel ----------------------------------------------


def test_apply_glass_bezel_off_macos_never_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-op is decided before the native handle is even read."""
    monkeypatch.setattr(_support.sys, "platform", "linux")
    sends = _faked_sends(monkeypatch)

    apply_glass_bezel(_button(0x1234))

    assert sends == []


def test_apply_glass_bezel_on_macos_25_never_dispatches(monkeypatch: pytest.MonkeyPatch) -> None:
    """An older macOS keeps its native bezel -- no message is sent."""
    monkeypatch.setattr(_support.sys, "platform", "darwin")
    monkeypatch.setattr(_support, "_macos_major", lambda: 25)
    sends = _faked_sends(monkeypatch)

    apply_glass_bezel(_button(0x1234))

    assert sends == []


def test_apply_glass_bezel_before_the_button_is_realized_never_dispatches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``GetHandle()`` is 0 before the dialog is shown -- no send."""
    monkeypatch.setattr(_support, "_glass_bezel_supported", lambda: True)
    sends = _faked_sends(monkeypatch)

    apply_glass_bezel(_button(0))

    assert sends == []


def test_apply_glass_bezel_on_macos_26_dispatches_the_glass_style_with_the_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The realized pointer reaches the native send with style 16."""
    monkeypatch.setattr(_support, "_glass_bezel_supported", lambda: True)
    sends = _faked_sends(monkeypatch)

    apply_glass_bezel(_button(0x4BEEF))

    assert sends == [(0x4BEEF, GLASS_BEZEL_STYLE)]


# --- _send_set_bezel_style's no-libobjc guard ------------------------


def test_send_set_bezel_style_given_no_libobjc_loads_no_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without libobjc the send is skipped, never crashing."""
    loaded: list[str] = []
    monkeypatch.setattr(_support.ctypes.util, "find_library", lambda _name: None)
    monkeypatch.setattr(_support.ctypes, "CDLL", loaded.append)

    _support._send_set_bezel_style(0x1234, GLASS_BEZEL_STYLE)

    assert loaded == []
