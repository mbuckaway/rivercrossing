# SPDX-License-Identifier: GPL-3.0-only
"""Verbose diagnostic log (F1): one NDJSON record per line.

The always-on crash log (``rivercrossing.log``, app.py's
``_append_exception_log``) captures uncaught exceptions and nothing
else. This module writes the *other* file --
``rivercrossing-verbose.log`` -- the opt-out trace of what the
operator did (menus, dialogs, buttons) and what the app built
code-side, which is what a support session needs to reproduce a
report.

The stdlib ``logging`` module does the work: one ``logging.Logger``
with a DEBUG-level ``logging.FileHandler`` whose
:class:`_NdjsonFormatter` renders each record as a single JSON
object, so a support session can filter it (``jq``, a spreadsheet)
instead of reading prose.

stdlib-only: no ``wx`` may reach this module, so it stays unit
testable headless.
"""

import json
import logging
import traceback
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

__all__ = ["VERBOSE_LOG_NAME", "VerboseLog"]

# The verbose file's name. Deliberately not the crash log's: the two
# files answer different questions and a support session reads them
# separately.
VERBOSE_LOG_NAME = "rivercrossing-verbose.log"

# One logger name for the whole process. Constructing a second
# VerboseLog (a relaunch inside one process, or a test) takes the
# handler over and closes the previous file, so exactly one verbose
# file is open at a time and a record can never land in two files.
_LOGGER_NAME = "rivercrossing.verbose"


class _NdjsonFormatter(logging.Formatter):
    """Render one ``LogRecord`` as one JSON object (NDJSON)."""

    def format(self, record: logging.LogRecord) -> str:
        """Return *record* as a single-line JSON object.

        The record's own fields become the documented keys: ``ts``
        (the record's instant, ISO-8601 UTC), ``level``, ``file``,
        ``line``, ``func`` and ``msg``. An error record adds ``exc``,
        the traceback as ``traceback.format_exception``'s list of
        lines. ``json.dumps`` escapes a newline inside a message,
        which is what keeps the file one record per line.
        """
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "file": record.filename,
            "line": record.lineno,
            "func": record.funcName,
            "msg": record.getMessage(),
        }
        if record.exc_info is not None:
            payload["exc"] = traceback.format_exception(*record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class VerboseLog:
    """The verbose NDJSON log: the app's own trace of what it just did.

    One instance per launch. Every public method calls its logger
    directly with ``stacklevel=2`` -- a single hop, so
    ``file``/``line``/``func`` name the app frame that logged rather
    than this wrapper.

    Writing is append-only across relaunches; a failed write is
    handled by the stdlib handler's own error path and never
    re-raised into the app.
    """

    def __init__(self, path: Path, *, enabled: bool = True) -> None:
        """Open *path* as this process's verbose log.

        Args:
            path: The NDJSON file to append to; the app passes the
                per-user config directory's
                ``rivercrossing-verbose.log`` (next to
                ``settings.json`` and the crash log). A missing
                parent directory is created -- a first launch has
                none yet.
            enabled: The starting state, seeded from
                ``AppSettings.verbose_logging``; :meth:`set_enabled`
                toggles it live.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        # delay=True: the file is opened by the first record, so a
        # session that logs nothing leaves no empty file behind.
        handler = logging.FileHandler(path, encoding="utf-8", delay=True)
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(_NdjsonFormatter())

        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.DEBUG)
        # Records belong to this file only; the root logger's
        # handlers (a test run's own) must never see them.
        logger.propagate = False
        for previous in list(logger.handlers):
            logger.removeHandler(previous)
            previous.close()
        logger.addHandler(handler)
        logger.disabled = not enabled
        self._logger = logger

    @property
    def enabled(self) -> bool:
        """Whether records are currently written."""
        return not self._logger.disabled

    def set_enabled(self, enabled: bool) -> None:  # noqa: FBT001 -- single unambiguous flag
        """Turn the log on or off (the settings dialog's live toggle).

        Args:
            enabled: ``True`` writes records, ``False`` stops them.
        """
        self._logger.disabled = not enabled

    def start(self, msg: str) -> None:
        """Record a launch/start marker.

        Args:
            msg: The message, e.g. ``"RiverCrossing launching"``.
        """
        self._logger.debug(msg, stacklevel=2)

    def menu(self, item_id: int, menu: str, label: str) -> None:
        """Record a §15 menu selection.

        Args:
            item_id: The wx menu item's event id.
            menu: The menu's own name, e.g. ``"File"``.
            label: The item's visible label.
        """
        self._logger.debug("menu %s: %s (id=%s)", menu, label, item_id, stacklevel=2)

    def dialog(self, name: str, opener: str) -> None:
        """Record a dialog opening.

        Args:
            name: The dialog's frozen XRC name.
            opener: What opened it -- the menu item or control name.
        """
        self._logger.debug("dialog %s (from %s)", name, opener, stacklevel=2)

    def button(self, name: str, label: str) -> None:
        """Record a button activation.

        Args:
            name: The button's frozen XRC name.
            label: The button's visible label.
        """
        self._logger.debug("button %s: %s", name, label, stacklevel=2)

    def control(self, name: str, kind: str) -> None:
        """Record a control the app built code-side.

        Args:
            name: The frozen name applied with ``SetName``.
            kind: The control's class name, e.g. ``"TextCtrl"``.
        """
        self._logger.debug("control %s: %s", name, kind, stacklevel=2)

    def exception(
        self,
        exc_type: type[BaseException],
        exc_value: BaseException,
        exc_tb: TracebackType | None,
    ) -> None:
        """Record an uncaught exception with its formatted traceback.

        Args:
            exc_type: The exception class.
            exc_value: The exception instance.
            exc_tb: The traceback, or ``None`` for a re-raised value.
        """
        self._logger.error(
            "%s: %s",
            exc_type.__name__,
            exc_value,
            exc_info=(exc_type, exc_value, exc_tb),
            stacklevel=2,
        )
