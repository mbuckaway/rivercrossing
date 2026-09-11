# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the verbose NDJSON diagnostic log (F1).

``rivercrossing.ui.logging`` is stdlib-only -- no ``wx`` reaches it,
so the whole surface runs on the host: each test drives a real
:class:`VerboseLog` against a ``tmp_path`` file and reads the NDJSON
back.

Two behaviours the support story depends on are pinned here: every
record is one JSON object carrying ``ts``/``level``/``file``/
``line``/``func``/``msg`` (plus ``exc`` on an error), and the
``stacklevel=2`` single hop -- a record names the *app* frame that
logged, never this wrapper.
"""

from __future__ import annotations

import inspect
import json
import logging
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui import logging as verbose_log_module
from rivercrossing.ui.logging import VERBOSE_LOG_NAME, VerboseLog

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType


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


# --- enabled / disabled ---------------------------------------------


def test_verbose_log_is_enabled_by_default(tmp_path: Path) -> None:
    """A fresh log starts on (verbose_logging defaults True)."""
    verbose = VerboseLog(tmp_path / VERBOSE_LOG_NAME)

    assert verbose.enabled is True


def test_verbose_log_with_enabled_false_reports_disabled(tmp_path: Path) -> None:
    """The constructor's enabled flag seeds the property."""
    verbose = VerboseLog(tmp_path / VERBOSE_LOG_NAME, enabled=False)

    assert verbose.enabled is False


@pytest.mark.parametrize("enabled", [True, False])
def test_set_enabled_given_each_state_updates_the_property(
    tmp_path: Path, *, enabled: bool
) -> None:
    """set_enabled is the single switch the settings dialog drives."""
    verbose = VerboseLog(tmp_path / VERBOSE_LOG_NAME, enabled=not enabled)

    verbose.set_enabled(enabled)

    assert verbose.enabled is enabled


def test_disabled_log_writes_no_records(tmp_path: Path) -> None:
    """Unticking verbose logging must leave the file empty."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path, enabled=False)

    verbose.start("ride opened")

    assert _records(path) == []


def test_log_disabled_mid_session_writes_only_the_earlier_record(tmp_path: Path) -> None:
    """Toggling off mid-session stops the writes from that point on."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)
    verbose.start("before")

    verbose.set_enabled(False)
    verbose.start("after")

    assert [record["msg"] for record in _records(path)] == ["before"]


def test_log_enabled_mid_session_writes_the_new_record(tmp_path: Path) -> None:
    """Toggling on mid-session starts the writes from that point on."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path, enabled=False)

    verbose.set_enabled(True)
    verbose.start("resumed")

    assert [record["msg"] for record in _records(path)] == ["resumed"]


# --- record shape (NDJSON, documented fields, stacklevel) -----------


def test_start_writes_the_documented_field_set(tmp_path: Path) -> None:
    """A plain record carries exactly ts/level/file/line/func/msg."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.start("ride opened")

    assert set(_records(path)[0]) == {"ts", "level", "file", "line", "func", "msg"}


def test_start_record_level_is_debug(tmp_path: Path) -> None:
    """Everything but an exception is DEBUG in the verbose log."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.start("ride opened")

    assert _records(path)[0]["level"] == "DEBUG"


def test_start_record_carries_the_given_message(tmp_path: Path) -> None:
    """The message reaches the NDJSON verbatim."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.start("ride opened")

    assert _records(path)[0]["msg"] == "ride opened"


def test_start_record_names_the_calling_frame_not_the_wrapper(tmp_path: Path) -> None:
    """stacklevel=2: the record points at the app frame that logged."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)
    expected_line = inspect.currentframe().f_lineno + 1
    verbose.start("ride opened")

    record = _records(path)[0]
    assert (record["file"], record["func"], record["line"]) == (
        "test_logging.py",
        "test_start_record_names_the_calling_frame_not_the_wrapper",
        expected_line,
    )


def test_start_record_ts_is_an_iso8601_utc_instant(tmp_path: Path) -> None:
    """The ts field is ISO-8601, inside the call's own window."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)
    before = datetime.now(UTC)
    verbose.start("ride opened")
    after = datetime.now(UTC)

    stamp = datetime.fromisoformat(str(_records(path)[0]["ts"]))

    assert before <= stamp <= after


def test_start_given_three_messages_writes_one_line_each(tmp_path: Path) -> None:
    """NDJSON: one JSON object per line, in order."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.start("one")
    verbose.start("two")
    verbose.start("three")

    assert [record["msg"] for record in _records(path)] == ["one", "two", "three"]


def test_start_with_empty_message_writes_the_empty_msg_field(tmp_path: Path) -> None:
    """An empty message is still a well-formed record (boundary)."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.start("")

    assert _records(path)[0]["msg"] == ""


def test_start_with_a_multiline_message_keeps_one_record_per_line(tmp_path: Path) -> None:
    """Embedded newlines are JSON-escaped, so the file stays NDJSON."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.start("line one\nline two")

    assert [record["msg"] for record in _records(path)] == ["line one\nline two"]


def test_init_creates_a_missing_parent_directory(tmp_path: Path) -> None:
    """A first launch has no config directory yet; the log makes it."""
    path = tmp_path / "config" / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.start("ride opened")

    assert [record["msg"] for record in _records(path)] == ["ride opened"]


def test_second_log_appends_to_the_same_file(tmp_path: Path) -> None:
    """A relaunch keeps the earlier session's records (mode="a")."""
    path = tmp_path / VERBOSE_LOG_NAME

    VerboseLog(path).start("first run")
    VerboseLog(path).start("second run")

    assert [record["msg"] for record in _records(path)] == ["first run", "second run"]


def test_second_log_writes_only_to_its_own_file(tmp_path: Path) -> None:
    """One verbose file per process: the new log takes it over."""
    first = tmp_path / "first.log"
    second = tmp_path / "second.log"
    VerboseLog(first).start("one")

    VerboseLog(second).start("two")

    assert [record["msg"] for record in _records(first)] == ["one"]
    assert [record["msg"] for record in _records(second)] == ["two"]


def test_verbose_log_name_is_distinct_from_the_crash_log() -> None:
    """Not the always-on plain-text crash log (rivercrossing.log)."""
    assert VERBOSE_LOG_NAME == "rivercrossing-verbose.log"
    assert VERBOSE_LOG_NAME != "rivercrossing.log"


# --- exception records ----------------------------------------------


def test_exception_writes_an_error_record_with_the_formatted_traceback(
    tmp_path: Path,
) -> None:
    """An uncaught error lands as ERROR plus its formatted traceback."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)
    exc_type, exc_value, exc_tb = _captured_exception()

    verbose.exception(exc_type, exc_value, exc_tb)

    record = _records(path)[0]
    assert (record["level"], record["msg"]) == ("ERROR", "RuntimeError: probe boom")
    assert record["exc"][0] == "Traceback (most recent call last):\n"
    assert record["exc"][-1] == "RuntimeError: probe boom\n"


def test_exception_record_names_the_calling_frame_not_the_wrapper(tmp_path: Path) -> None:
    """stacklevel=2 holds on the error path too."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)
    exc_type, exc_value, exc_tb = _captured_exception()
    expected_line = inspect.currentframe().f_lineno + 1
    verbose.exception(exc_type, exc_value, exc_tb)

    record = _records(path)[0]
    assert (record["file"], record["func"], record["line"]) == (
        "test_logging.py",
        "test_exception_record_names_the_calling_frame_not_the_wrapper",
        expected_line,
    )


# --- the named-selection helpers ------------------------------------


def test_menu_logs_the_item_id_menu_and_label(tmp_path: Path) -> None:
    """A §15 menu selection records its id, menu and label."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.menu(5001, "File", "Settings…")

    assert _records(path)[0]["msg"] == "menu File: Settings… (id=5001)"


@pytest.mark.parametrize("item_id", [0, 1, 5000, 5001, 2**31 - 1])
def test_menu_logs_each_item_id_verbatim(tmp_path: Path, item_id: int) -> None:
    """The wx event id reaches the record unmangled (0 to 2**31-1)."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.menu(item_id, "View", "Hide times")

    assert _records(path)[0]["msg"] == f"menu View: Hide times (id={item_id})"


def test_dialog_logs_the_window_name_and_its_opener(tmp_path: Path) -> None:
    """A dialog records its frozen name and what opened it."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.dialog("settings_dlg", "mi_settings")

    assert _records(path)[0]["msg"] == "dialog settings_dlg (from mi_settings)"


def test_button_logs_the_control_name_and_label(tmp_path: Path) -> None:
    """A button click records its frozen name and visible label."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.button("backup_now_btn", "Back up now")

    assert _records(path)[0]["msg"] == "button backup_now_btn: Back up now"


def test_control_logs_the_frozen_name_and_its_kind(tmp_path: Path) -> None:
    """A built control records its frozen name and wx class."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.control("plate_input", "TextCtrl")

    assert _records(path)[0]["msg"] == "control plate_input: TextCtrl"


def test_helper_records_are_debug_level(tmp_path: Path) -> None:
    """The selection helpers are DEBUG like every non-error record."""
    path = tmp_path / VERBOSE_LOG_NAME
    verbose = VerboseLog(path)

    verbose.menu(5001, "File", "Settings…")
    verbose.dialog("settings_dlg", "mi_settings")
    verbose.button("backup_now_btn", "Back up now")
    verbose.control("plate_input", "TextCtrl")

    assert [record["level"] for record in _records(path)] == ["DEBUG"] * 4


# --- property: the JSON round-trip (T-7) ----------------------------


@given(st.text())
def test_ndjson_formatter_given_any_message_round_trips_it(message: str) -> None:
    """Property: any message survives json.dumps -> json.loads intact.

    The formatter is this module's only pure function, and its
    invariant is the round-trip the support tooling relies on:
    whatever the app logs comes back out of ``json.loads`` unchanged.
    """
    record = logging.LogRecord(
        name="rivercrossing.verbose",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg=message,
        args=(),
        exc_info=None,
    )

    line = verbose_log_module._NdjsonFormatter().format(record)

    assert json.loads(line)["msg"] == message
