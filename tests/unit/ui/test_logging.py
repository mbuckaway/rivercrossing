# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for the per-invocation NDJSON diagnostic log.

``rivercrossing.ui.logging`` is stdlib-only -- no ``wx`` reaches it,
so the whole surface runs on the host: each test drives a real
:class:`Logging` against a ``tmp_path`` file and reads the NDJSON
back.

Three contracts are pinned here: every record is one JSON object
carrying ``ts``/``level``/``event``/``file``/``line``/``func`` plus
the call's own fields; the ``stacklevel=2`` single hop names the
*app* frame that logged, never this wrapper; and the always-on
records (``app_start``, ``launch``, ``ride_loaded``, ``exception``)
survive ``verbose=False`` while the trace methods do not.
"""

from __future__ import annotations

import inspect
import json
import logging
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ui import logging as invocation_log_module
from rivercrossing.ui.logging import (
    LOG_BASENAME,
    Logging,
    build_log_path,
    prune_logs,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from types import TracebackType


def _log_path(directory: Path) -> Path:
    """Return the invocation log path for *directory*."""
    return build_log_path(directory, datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC))


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


def _write_logs(directory: Path, count: int) -> list[Path]:
    """Create *count* matching logs with strictly increasing mtimes."""
    base = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)
    paths: list[Path] = []
    for index in range(count):
        path = build_log_path(directory, base + timedelta(seconds=index))
        path.write_text("{}\n", encoding="utf-8")
        os.utime(path, (base.timestamp() + index, base.timestamp() + index))
        paths.append(path)
    return paths


# --- verbose flag ---------------------------------------------------


def test_logging_is_verbose_by_default(tmp_path: Path) -> None:
    """A fresh log traces by default (verbose_logging defaults True)."""
    log = Logging(_log_path(tmp_path))

    assert log.verbose is True


def test_logging_with_verbose_false_reports_not_verbose(tmp_path: Path) -> None:
    """The constructor's verbose flag seeds the property."""
    log = Logging(_log_path(tmp_path), verbose=False)

    assert log.verbose is False


@pytest.mark.parametrize("verbose", [True, False])
def test_set_verbose_given_each_state_updates_the_property(
    tmp_path: Path,
    verbose: bool,  # noqa: FBT001 -- a parametrize row's value, not a call-site bool
) -> None:
    """set_verbose is the single switch the settings dialog drives."""
    log = Logging(_log_path(tmp_path), verbose=not verbose)

    log.set_verbose(verbose)

    assert log.verbose is verbose


def test_set_verbose_false_mid_session_writes_no_further_trace_records(
    tmp_path: Path,
) -> None:
    """Toggling the trace off mid-session stops later records."""
    path = _log_path(tmp_path)
    log = Logging(path)
    log.marker("before")

    log.set_verbose(False)
    log.marker("after")

    assert [record["msg"] for record in _records(path)] == ["before"]


def test_set_verbose_true_mid_session_writes_the_next_trace_record(
    tmp_path: Path,
) -> None:
    """Toggling the trace on mid-session starts later records."""
    path = _log_path(tmp_path)
    log = Logging(path, verbose=False)

    log.set_verbose(True)
    log.marker("resumed")

    assert [record["event"] for record in _records(path)] == ["marker"]


# --- file lifecycle -------------------------------------------------


def test_init_creates_the_log_file_immediately_with_no_records(tmp_path: Path) -> None:
    """Mode "w", no delay: the file exists and is empty at launch."""
    path = _log_path(tmp_path)

    Logging(path)

    assert path.exists()
    assert path.read_text(encoding="utf-8") == ""


def test_init_creates_a_missing_parent_directory(tmp_path: Path) -> None:
    """A first launch has no config directory yet; the log makes it."""
    path = tmp_path / "config" / "rivercrossing-20260911-120000.log"
    log = Logging(path)

    log.marker("ride opened")

    assert [record["event"] for record in _records(path)] == ["marker"]


def test_init_given_an_existing_file_overwrites_it(tmp_path: Path) -> None:
    """Each invocation gets its own contents, never the last run's."""
    path = _log_path(tmp_path)
    path.write_text('{"stale": true}\n', encoding="utf-8")
    log = Logging(path)

    log.marker("fresh")

    assert [record["msg"] for record in _records(path)] == ["fresh"]


def test_close_flushes_the_written_records_to_disk(tmp_path: Path) -> None:
    """close() leaves every record the invocation wrote on disk."""
    path = _log_path(tmp_path)
    log = Logging(path, verbose=False)
    log.startup(
        verbose=False,
        started_at=datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC),
        version="1.0.12",
        platform="darwin",
        python="3.14.0",
        pid=4321,
    )

    log.close()

    assert [record["event"] for record in _records(path)] == ["app_start"]


def test_close_closes_the_file_handler(tmp_path: Path) -> None:
    """A closed log holds no open file handle."""
    log = Logging(_log_path(tmp_path))
    handler = log._logger.handlers[0]

    log.close()

    assert handler.stream is None


def test_second_log_writes_only_to_its_own_file(tmp_path: Path) -> None:
    """One invocation per file: the new log takes the logger over."""
    first = build_log_path(tmp_path, datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC))
    second = build_log_path(tmp_path, datetime(2026, 9, 11, 12, 0, 1, tzinfo=UTC))
    Logging(first).marker("one")

    Logging(second).marker("two")

    assert [record["msg"] for record in _records(first)] == ["one"]
    assert [record["msg"] for record in _records(second)] == ["two"]


# --- trace records (verbose=True) -----------------------------------


def test_marker_writes_the_documented_field_set(tmp_path: Path) -> None:
    """A marker record is the base six fields plus the call's own."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.marker("ride opened")

    record = _records(path)[0]
    assert set(record) == {"ts", "level", "event", "file", "line", "func", "msg"}


def test_marker_record_level_is_debug(tmp_path: Path) -> None:
    """Every trace record is DEBUG."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.marker("ride opened")

    assert _records(path)[0]["level"] == "DEBUG"


def test_marker_record_carries_the_event_and_message(tmp_path: Path) -> None:
    """A marker names its event and preserves the message verbatim."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.marker("ride opened")

    record = _records(path)[0]
    assert (record["event"], record["msg"]) == ("marker", "ride opened")


def test_marker_record_names_the_calling_frame_not_the_wrapper(tmp_path: Path) -> None:
    """stacklevel=2: the record points at the app frame that logged."""
    path = _log_path(tmp_path)
    log = Logging(path)
    expected_line = inspect.currentframe().f_lineno + 1
    log.marker("ride opened")

    record = _records(path)[0]
    assert (record["file"], record["func"], record["line"]) == (
        "test_logging.py",
        "test_marker_record_names_the_calling_frame_not_the_wrapper",
        expected_line,
    )


def test_marker_record_ts_is_an_iso8601_utc_instant(tmp_path: Path) -> None:
    """The ts field is ISO-8601, inside the call's own window."""
    path = _log_path(tmp_path)
    log = Logging(path)
    before = datetime.now(UTC)
    log.marker("ride opened")
    after = datetime.now(UTC)

    stamp = datetime.fromisoformat(str(_records(path)[0]["ts"]))

    assert before <= stamp <= after


def test_marker_given_three_messages_writes_one_line_each(tmp_path: Path) -> None:
    """NDJSON: one JSON object per line, in order."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.marker("one")
    log.marker("two")
    log.marker("three")

    assert [record["msg"] for record in _records(path)] == ["one", "two", "three"]


def test_marker_with_empty_message_writes_the_empty_msg_field(tmp_path: Path) -> None:
    """An empty message is still a well-formed record (boundary)."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.marker("")

    assert _records(path)[0]["msg"] == ""


def test_marker_with_a_multiline_message_keeps_one_record_per_line(
    tmp_path: Path,
) -> None:
    """Embedded newlines are JSON-escaped, so the file stays NDJSON."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.marker("line one\nline two")

    assert [record["msg"] for record in _records(path)] == ["line one\nline two"]


def test_menu_logs_the_item_id_menu_and_label(tmp_path: Path) -> None:
    """A §15 menu selection records its id, menu and label."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.menu(5001, "File", "Settings…")

    record = _records(path)[0]
    assert (
        record["event"],
        record["item_id"],
        record["menu"],
        record["label"],
    ) == ("menu", 5001, "File", "Settings…")


@pytest.mark.parametrize("item_id", [0, 1, 2, 5001, 2**31 - 2, 2**31 - 1])
def test_menu_given_each_item_id_logs_it_verbatim(tmp_path: Path, item_id: int) -> None:
    """The wx event id reaches the record unmangled (0 to 2**31-1)."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.menu(item_id, "View", "Hide times")

    assert _records(path)[0]["item_id"] == item_id


def test_dialog_logs_the_name_and_opener(tmp_path: Path) -> None:
    """A dialog records its frozen name and what opened it."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.dialog("settings_dlg", "mi_settings")

    record = _records(path)[0]
    assert (record["event"], record["name"], record["opener"]) == (
        "dialog",
        "settings_dlg",
        "mi_settings",
    )


def test_button_logs_the_name_and_label(tmp_path: Path) -> None:
    """A button click records its frozen name and visible label."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.button("backup_now_btn", "Back up now")

    record = _records(path)[0]
    assert (record["event"], record["name"], record["label"]) == (
        "button",
        "backup_now_btn",
        "Back up now",
    )


def test_control_logs_the_name_and_kind(tmp_path: Path) -> None:
    """A built control records its frozen name and wx class."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.control("plate_input", "TextCtrl")

    record = _records(path)[0]
    assert (record["event"], record["name"], record["kind"]) == (
        "control",
        "plate_input",
        "TextCtrl",
    )


# --- the always-on records ------------------------------------------


def test_startup_writes_an_info_app_start_record(tmp_path: Path) -> None:
    """The launch context lands as one INFO app_start record."""
    path = _log_path(tmp_path)
    log = Logging(path)
    started_at = datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC)

    log.startup(
        verbose=True,
        started_at=started_at,
        version="1.0.12",
        platform="darwin",
        python="3.14.0",
        pid=4321,
    )

    record = _records(path)[0]
    assert set(record) == {
        "ts",
        "level",
        "event",
        "file",
        "line",
        "func",
        "verbose",
        "started_at",
        "version",
        "platform",
        "python",
        "pid",
    }
    assert (
        record["level"],
        record["event"],
        record["verbose"],
        record["started_at"],
        record["version"],
        record["platform"],
        record["python"],
        record["pid"],
    ) == (
        "INFO",
        "app_start",
        True,
        "2026-09-11T12:00:00+00:00",
        "1.0.12",
        "darwin",
        "3.14.0",
        4321,
    )


def test_launch_writes_an_info_launch_record(tmp_path: Path) -> None:
    """The resume/start choice lands as one INFO launch record."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.launch(previous_state="ride_open", previous_ride_id=7, choice="resume")

    record = _records(path)[0]
    assert (
        record["level"],
        record["event"],
        record["previous_state"],
        record["previous_ride_id"],
        record["choice"],
    ) == ("INFO", "launch", "ride_open", 7, "resume")


@pytest.mark.parametrize("previous_ride_id", [None, 0, 1, 7])
def test_launch_given_each_previous_ride_id_logs_it_verbatim(
    tmp_path: Path, previous_ride_id: int | None
) -> None:
    """A first launch has no previous ride; a resumed one has its id."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.launch(
        previous_state="no_ride",
        previous_ride_id=previous_ride_id,
        choice="new_ride",
    )

    assert _records(path)[0]["previous_ride_id"] == previous_ride_id


def test_ride_loaded_writes_an_info_ride_loaded_record(tmp_path: Path) -> None:
    """A loaded ride lands as one INFO ride_loaded record."""
    path = _log_path(tmp_path)
    log = Logging(path)

    log.ride_loaded(ride_id=7)

    record = _records(path)[0]
    assert (record["level"], record["event"], record["ride_id"]) == (
        "INFO",
        "ride_loaded",
        7,
    )


_ALWAYS_ON_CALLS: dict[str, Callable[[Logging], None]] = {
    "app_start": lambda log: log.startup(
        verbose=False,
        started_at=datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC),
        version="1.0.12",
        platform="darwin",
        python="3.14.0",
        pid=4321,
    ),
    "launch": lambda log: log.launch(
        previous_state="no_ride", previous_ride_id=None, choice="new_ride"
    ),
    "ride_loaded": lambda log: log.ride_loaded(ride_id=7),
}


@pytest.mark.parametrize("event", list(_ALWAYS_ON_CALLS))
def test_always_on_method_with_verbose_false_still_writes_its_record(
    tmp_path: Path, event: str
) -> None:
    """Opting out of the trace never hides the launch context."""
    path = _log_path(tmp_path)
    log = Logging(path, verbose=False)

    _ALWAYS_ON_CALLS[event](log)

    assert [record["event"] for record in _records(path)] == [event]


# --- exception records ----------------------------------------------


def test_exception_writes_an_error_record_with_the_formatted_traceback(
    tmp_path: Path,
) -> None:
    """An uncaught error lands as ERROR plus its formatted traceback."""
    path = _log_path(tmp_path)
    log = Logging(path)
    exc_type, exc_value, exc_tb = _captured_exception()

    log.exception(exc_type, exc_value, exc_tb)  # noqa: LOG004 -- takes an explicit exc triple

    record = _records(path)[0]
    assert (
        record["level"],
        record["event"],
        record["exc_type"],
        record["exc_message"],
    ) == ("ERROR", "exception", "RuntimeError", "probe boom")
    assert record["traceback"][0] == "Traceback (most recent call last):\n"
    assert record["traceback"][-1] == "RuntimeError: probe boom\n"


def test_exception_record_names_the_calling_frame_not_the_wrapper(
    tmp_path: Path,
) -> None:
    """stacklevel=2 holds on the error path too."""
    path = _log_path(tmp_path)
    log = Logging(path)
    exc_type, exc_value, exc_tb = _captured_exception()
    expected_line = inspect.currentframe().f_lineno + 1
    log.exception(exc_type, exc_value, exc_tb)  # noqa: LOG004 -- takes an explicit exc triple

    record = _records(path)[0]
    assert (record["file"], record["func"], record["line"]) == (
        "test_logging.py",
        "test_exception_record_names_the_calling_frame_not_the_wrapper",
        expected_line,
    )


def test_exception_with_verbose_false_still_writes_the_error_record(
    tmp_path: Path,
) -> None:
    """A crash is always logged, whatever the trace setting."""
    path = _log_path(tmp_path)
    log = Logging(path, verbose=False)
    exc_type, exc_value, exc_tb = _captured_exception()

    log.exception(exc_type, exc_value, exc_tb)  # noqa: LOG004 -- takes an explicit exc triple

    assert [record["event"] for record in _records(path)] == ["exception"]


_TRACE_CALLS: dict[str, Callable[[Logging], None]] = {
    "marker": lambda log: log.marker("ride opened"),
    "menu": lambda log: log.menu(5001, "File", "Settings…"),
    "dialog": lambda log: log.dialog("settings_dlg", "mi_settings"),
    "button": lambda log: log.button("backup_now_btn", "Back up now"),
    "control": lambda log: log.control("plate_input", "TextCtrl"),
}


@pytest.mark.parametrize("event", list(_TRACE_CALLS))
def test_trace_method_with_verbose_false_writes_no_records(tmp_path: Path, event: str) -> None:
    """Unticking verbose logging leaves the trace out of the file."""
    path = _log_path(tmp_path)
    log = Logging(path, verbose=False)

    _TRACE_CALLS[event](log)

    assert _records(path) == []


@pytest.mark.parametrize("event", list(_TRACE_CALLS))
def test_trace_method_with_verbose_true_writes_its_own_event(tmp_path: Path, event: str) -> None:
    """Every trace method names itself in the event field."""
    path = _log_path(tmp_path)
    log = Logging(path)

    _TRACE_CALLS[event](log)

    assert [record["event"] for record in _records(path)] == [event]


# --- build_log_path -------------------------------------------------


def test_log_basename_is_rivercrossing() -> None:
    """The shared basename the glob and the file name are built from."""
    assert LOG_BASENAME == "rivercrossing"


@pytest.mark.parametrize(
    ("now", "expected_name"),
    [
        (
            datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
            "rivercrossing-20260102-030405.log",
        ),
        (
            datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC),
            "rivercrossing-20260911-120000.log",
        ),
        (
            datetime(2026, 12, 31, 23, 59, 59, tzinfo=UTC),
            "rivercrossing-20261231-235959.log",
        ),
    ],
)
def test_build_log_path_given_each_instant_names_the_file_with_its_timestamp(
    tmp_path: Path, now: datetime, expected_name: str
) -> None:
    """The file name is the launch instant to the second."""
    assert build_log_path(tmp_path, now).name == expected_name


def test_build_log_path_keeps_the_file_inside_the_given_directory(
    tmp_path: Path,
) -> None:
    """The log lives beside settings.json, not in the cwd."""
    path = build_log_path(tmp_path, datetime(2026, 9, 11, 12, 0, 0, tzinfo=UTC))

    assert path.parent == tmp_path


# --- prune_logs -----------------------------------------------------


def test_prune_logs_given_more_than_keep_deletes_the_oldest(tmp_path: Path) -> None:
    """Only the keep most recent invocations survive."""
    paths = _write_logs(tmp_path, 5)

    prune_logs(tmp_path, keep=2)

    assert {path.name for path in tmp_path.iterdir()} == {path.name for path in paths[-2:]}


@pytest.mark.parametrize(("count", "keep"), [(3, 0), (3, 1), (3, 2), (3, 3), (3, 4)])
def test_prune_logs_given_each_keep_retains_the_newest_n(
    tmp_path: Path,
    count: int,
    keep: int,
) -> None:
    """Keep 0 deletes all; keep >= count deletes nothing."""
    paths = _write_logs(tmp_path, count)

    prune_logs(tmp_path, keep=keep)

    assert {path.name for path in tmp_path.iterdir()} == {
        path.name for path in paths[count - min(count, keep) :]
    }


def test_prune_logs_given_fewer_logs_than_the_default_keeps_them_all(
    tmp_path: Path,
) -> None:
    """The default keeps the last 20 invocations."""
    paths = _write_logs(tmp_path, 3)

    prune_logs(tmp_path)

    assert {path.name for path in tmp_path.iterdir()} == {path.name for path in paths}


def test_prune_logs_keeps_the_newest_by_mtime_not_by_name(tmp_path: Path) -> None:
    """Pruning follows modification time, not the embedded timestamp."""
    first_named = build_log_path(tmp_path, datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
    second_named = build_log_path(tmp_path, datetime(2026, 1, 2, 0, 0, 0, tzinfo=UTC))
    first_named.write_text("{}\n", encoding="utf-8")
    second_named.write_text("{}\n", encoding="utf-8")
    os.utime(first_named, (2000.0, 2000.0))
    os.utime(second_named, (1000.0, 1000.0))

    prune_logs(tmp_path, keep=1)

    assert {path.name for path in tmp_path.iterdir()} == {first_named.name}


def test_prune_logs_ignores_unrelated_files(tmp_path: Path) -> None:
    """settings.json is not an invocation log."""
    paths = _write_logs(tmp_path, 3)
    other = tmp_path / "settings.json"
    other.write_text("{}", encoding="utf-8")

    prune_logs(tmp_path, keep=1)

    assert {path.name for path in tmp_path.iterdir()} == {
        paths[-1].name,
        other.name,
    }


def test_prune_logs_keeps_the_crash_log(tmp_path: Path) -> None:
    """The always-on crash log is not part of the pruned set."""
    crash = tmp_path / "rivercrossing.log"
    crash.write_text("boom\n", encoding="utf-8")
    _write_logs(tmp_path, 3)

    prune_logs(tmp_path, keep=1)

    assert crash.exists()


def test_prune_logs_on_a_missing_directory_deletes_nothing(tmp_path: Path) -> None:
    """A first launch has no directory yet; pruning must not raise."""
    missing = tmp_path / "absent"

    prune_logs(missing)

    assert not missing.exists()


# --- properties (T-7) -----------------------------------------------


@given(st.text())
def test_ndjson_formatter_given_any_field_round_trips_it(message: str) -> None:
    """Property: any field value survives json.dumps -> json.loads.

    The formatter is this module's only pure function, and its
    invariant is the round-trip the support tooling relies on:
    whatever the app logs comes back out of ``json.loads`` unchanged.
    """
    record = logging.LogRecord(
        name="rivercrossing.verbose",
        level=logging.DEBUG,
        pathname=__file__,
        lineno=1,
        msg="marker",
        args=(),
        exc_info=None,
    )
    record.event = "marker"
    record.fields = {"msg": message}

    line = invocation_log_module._NdjsonFormatter().format(record)

    assert json.loads(line)["msg"] == message


@given(st.datetimes(timezones=st.just(UTC)))
def test_build_log_path_given_any_instant_builds_the_documented_name(
    instant: datetime,
) -> None:
    """Property: the name is the basename plus the strftime stamp."""
    path = build_log_path(Path("logs"), instant)

    assert path.name == f"{LOG_BASENAME}-{instant:%Y%m%d-%H%M%S}.log"
