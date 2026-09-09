# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's uncaught-exception crash log.

wxPython 4.3.1 swallows a Python exception raised inside an event
handler after routing it through ``sys.excepthook`` (measured -- the
loop calls ``PyErr_Print``, and an ``OnExceptionInMainLoop`` override
is never dispatched), and the windowed bundle has no console to show
the default hook's stderr traceback: handler exceptions vanish with
zero signal. This module proves the fix headless:

- :func:`rivercrossing.ui.app._append_exception_log` writes the
  formatted traceback to the per-user log next to the settings file.
- the app subclass's ``_handle_uncaught_exception`` (the hook target)
  writes that log and posts a one-line status notice when a frame
  exists.
- :func:`rivercrossing.ui.app._install_crash_excepthook` wires that
  handler as ``sys.excepthook`` -- the one hook that fires for both
  the pre-MainLoop path and main-loop handler exceptions.

The app is the real ``RiverCrossingApp`` from :func:`build_app` (the
``test_app_*.py`` seam style: a live wx object, destroyed before the
test ends); only the crash-log path and the notice target are fakes.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rivercrossing.ui import app as app_module

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType


class _NoticeFrame:
    """A frame stub recording status-bar notices."""

    def __init__(self) -> None:
        """Start with no notices."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record *text* as the latest notice."""
        self.notices.append(text)


def _captured_exception() -> tuple[type[BaseException], BaseException, TracebackType | None]:
    """Return a real (type, value, traceback) triple from this frame."""

    def _raise_probe() -> None:
        raise RuntimeError("probe boom")

    try:
        _raise_probe()
    except RuntimeError:
        return sys.exc_info()


def test_append_exception_log_writes_the_formatted_traceback(tmp_path: Path) -> None:
    """The crash log records the exception class and its message."""
    log_path = tmp_path / "rivercrossing.log"
    exc_type, exc_value, exc_tb = _captured_exception()

    written = app_module._append_exception_log(log_path, exc_type, exc_value, exc_tb)

    assert written == log_path
    text = log_path.read_text(encoding="utf-8")
    assert "Traceback (most recent call last):" in text
    assert "RuntimeError: probe boom" in text
    assert "test_app_crash_log.py" in text


def test_app_uncaught_handler_writes_the_log_and_posts_a_notice(
    tmp_path: Path,
) -> None:
    """The hook target logs the traceback and tells the operator."""
    app = app_module.build_app()
    try:
        app.crash_log_path = tmp_path / "rivercrossing.log"
        app.main_frame = _NoticeFrame()
        exc_type, exc_value, exc_tb = _captured_exception()

        app._handle_uncaught_exception(exc_type, exc_value, exc_tb)

        assert "RuntimeError: probe boom" in app.crash_log_path.read_text(encoding="utf-8")
        assert app.main_frame.notices == [
            "An unexpected error occurred — see rivercrossing.log for details"
        ]
    finally:
        app.Destroy()


def test_app_uncaught_handler_with_no_frame_logs_without_a_notice(
    tmp_path: Path,
) -> None:
    """Pre-frame bootstrap exceptions still reach the crash log."""
    app = app_module.build_app()
    try:
        app.crash_log_path = tmp_path / "rivercrossing.log"
        app.main_frame = None
        exc_type, exc_value, exc_tb = _captured_exception()

        app._handle_uncaught_exception(exc_type, exc_value, exc_tb)

        assert "RuntimeError: probe boom" in app.crash_log_path.read_text(encoding="utf-8")
    finally:
        app.Destroy()


def test_install_crash_excepthook_wires_the_app_handler() -> None:
    """The installed hook forwards uncaught exceptions to the app."""
    calls: list[tuple[object, object, object]] = []

    class _FakeApp:
        """An app-like object recording handler invocations."""

        def _handle_uncaught_exception(
            self, exc_type: type[BaseException], exc_value: BaseException, exc_tb: object
        ) -> None:
            calls.append((exc_type, exc_value, exc_tb))

    original = sys.excepthook
    try:
        app_module._install_crash_excepthook(_FakeApp())

        sys.excepthook(ValueError, ValueError("bootstrap boom"), None)

        assert len(calls) == 1
        assert calls[0][0] is ValueError
        assert str(calls[0][1]) == "bootstrap boom"
        assert calls[0][2] is None
    finally:
        sys.excepthook = original
