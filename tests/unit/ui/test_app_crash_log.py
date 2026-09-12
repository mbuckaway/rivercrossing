# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's uncaught-exception logging.

wxPython 4.3.1 swallows a Python exception raised inside an event
handler after routing it through ``sys.excepthook`` (measured -- the
loop calls ``PyErr_Print``, and an ``OnExceptionInMainLoop`` override
is never dispatched), and the windowed bundle has no console to show
the default hook's stderr traceback: handler exceptions vanish with
zero signal. The separate plain-text crash log is gone (workstream
A2); the one per-invocation NDJSON log now carries the crash. This
module proves the fix headless:

- the app subclass's ``_handle_uncaught_exception`` (the hook target)
  writes an ``event="exception"`` record -- with the formatted
  traceback -- to ``app.log`` and posts a one-line status notice
  naming that file when a frame exists.
- :func:`rivercrossing.ui.app._install_crash_excepthook` wires that
  handler as ``sys.excepthook`` -- the one hook that fires for both
  the pre-MainLoop path and main-loop handler exceptions.

The app is the real ``RiverCrossingApp`` from :func:`build_app` (the
``test_app_*.py`` seam style: a live wx object, destroyed before the
test ends); only the log target and the notice target are fakes.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from rivercrossing.ui import app as app_module
from rivercrossing.ui.logging import Logging, build_log_path

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

# A fixed launch instant names the invocation log under test.
_LAUNCH = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)


class _NoticeFrame:
    """A frame stub recording status-bar notices."""

    def __init__(self) -> None:
        """Start with no notices."""
        self.notices: list[str] = []

    def SetStatusText(self, text: str) -> None:  # noqa: N802 -- wx API name
        """Record *text* as the latest notice."""
        self.notices.append(text)


def _log_path(directory: Path) -> Path:
    """Return this test's invocation log path inside *directory*."""
    return build_log_path(directory, _LAUNCH)


def _records(path: Path) -> list[dict[str, object]]:
    """Return the NDJSON records at *path* (none when it is absent)."""
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8")
    return [json.loads(line) for line in text.splitlines() if line]


def _captured_exception() -> tuple[type[BaseException], BaseException, TracebackType | None]:
    """Return a real (type, value, traceback) triple from this frame."""

    def _raise_probe() -> None:
        raise RuntimeError("probe boom")

    try:
        _raise_probe()
    except RuntimeError:
        return sys.exc_info()


def test_handle_uncaught_exception_writes_the_exception_record(tmp_path: Path) -> None:
    """The log records the exception class, message and traceback."""
    app = app_module.build_app()
    try:
        app.log = Logging(_log_path(tmp_path))
        exc_type, exc_value, exc_tb = _captured_exception()

        app._handle_uncaught_exception(exc_type, exc_value, exc_tb)

        record = _records(_log_path(tmp_path))[0]
        assert (record["level"], record["event"]) == ("ERROR", "exception")
        assert record["exc_type"] == "RuntimeError"
        assert record["exc_message"] == "probe boom"
        text = "".join(record["traceback"])
        assert "Traceback (most recent call last):" in text
        assert "RuntimeError: probe boom" in text
        assert "test_app_crash_log.py" in text
    finally:
        app.Destroy()


def test_handle_uncaught_exception_with_a_frame_posts_a_notice_naming_the_log(
    tmp_path: Path,
) -> None:
    """The hook target tells the operator which file holds the trace."""
    app = app_module.build_app()
    try:
        app.log = Logging(_log_path(tmp_path))
        app.main_frame = _NoticeFrame()
        exc_type, exc_value, exc_tb = _captured_exception()

        app._handle_uncaught_exception(exc_type, exc_value, exc_tb)

        assert app.main_frame.notices == [
            f"An unexpected error occurred — see {_log_path(tmp_path).name} for details"
        ]
    finally:
        app.Destroy()


def test_handle_uncaught_exception_without_a_frame_still_records_the_exception(
    tmp_path: Path,
) -> None:
    """Pre-frame bootstrap exceptions still reach the log."""
    app = app_module.build_app()
    try:
        app.log = Logging(_log_path(tmp_path))
        app.main_frame = None
        exc_type, exc_value, exc_tb = _captured_exception()

        app._handle_uncaught_exception(exc_type, exc_value, exc_tb)

        assert [record["event"] for record in _records(_log_path(tmp_path))] == ["exception"]
    finally:
        app.Destroy()


def test_handle_uncaught_exception_without_a_log_posts_no_notice(tmp_path: Path) -> None:
    """An app built without the launch log has nowhere to record."""
    app = app_module.build_app()
    try:
        app.log = None
        app.main_frame = _NoticeFrame()
        exc_type, exc_value, exc_tb = _captured_exception()

        app._handle_uncaught_exception(exc_type, exc_value, exc_tb)

        assert app.main_frame.notices == []
        assert list(tmp_path.iterdir()) == []
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
