# SPDX-License-Identifier: GPL-3.0-only
"""Per-invocation structured log (F1): one NDJSON record per line.

Each launch writes its own file, ``rivercrossing-<timestamp>.log``
(:func:`build_log_path`), in the per-user config directory. The file
holds exactly this invocation: :class:`Logging` opens it truncating and
:func:`prune_logs` deletes all but the most recent sessions. There is no
separate crash log -- exceptions go through :meth:`Logging.exception`.

Records come in two kinds:

* always-on -- :meth:`Logging.startup`, :meth:`Logging.launch`,
  :meth:`Logging.ride_loaded` and :meth:`Logging.exception` survive
  ``verbose=False``: a support session needs the launch context and any
  crash even when the operator opted out of the trace;
* trace -- :meth:`Logging.marker`, :meth:`Logging.menu`,
  :meth:`Logging.dialog`, :meth:`Logging.button` and
  :meth:`Logging.control` are the opt-out "what did the operator do"
  trail.

The stdlib ``logging`` module does the work: one ``logging.Logger``
whose :class:`_NdjsonFormatter` renders each record as a single JSON
object -- ``ts``/``level``/``event``/``file``/``line``/``func`` plus
the call's own fields -- so a support session can filter it (``jq``, a
spreadsheet) instead of reading prose.

stdlib-only: no ``wx`` may reach this module, so it stays unit testable
headless.
"""

import json
import logging
import traceback
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path
    from types import TracebackType

__all__ = ["LOG_BASENAME", "Logging", "build_log_path", "prune_logs"]

# Every invocation file's name is this basename plus its launch instant;
# prune_logs' glob is derived from it so the two cannot drift apart.
LOG_BASENAME = "rivercrossing"

# One logger name for the whole process. Constructing a second Logging
# (a relaunch inside one process, or a test) takes the handler over and
# closes the previous file, so exactly one invocation file is open at a
# time and a record can never land in more than one of them.
_LOGGER_NAME = "rivercrossing.verbose"


def build_log_path(directory: Path, now: datetime) -> Path:
    """Return this invocation's log path inside *directory*.

    Args:
        directory: The per-user config directory the log lives in,
            beside ``settings.json`` and the crash log.
        now: The launch instant, which names the file.

    Returns:
        ``rivercrossing-<YYYYmmdd-HHMMSS>.log`` inside *directory*.
    """
    return directory / f"{LOG_BASENAME}-{now:%Y%m%d-%H%M%S}.log"


def prune_logs(directory: Path, *, keep: int = 20) -> None:
    """Delete all but the *keep* most recent invocation logs.

    Args:
        directory: The config directory to prune. A missing directory
            is ignored -- a first launch has nothing to prune.
        keep: How many of the most recently modified matches to keep.
            ``0`` deletes them all.
    """
    if not directory.is_dir():
        return
    logs = sorted(
        directory.glob(f"{LOG_BASENAME}-*.log"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for path in logs[keep:]:
        path.unlink(missing_ok=True)


class _NdjsonFormatter(logging.Formatter):
    """Render one ``LogRecord`` as one JSON object (NDJSON)."""

    def format(self, record: logging.LogRecord) -> str:
        """Return *record* as a single-line JSON object.

        Every record carries ``ts`` (the record's instant, ISO-8601
        UTC), ``level``, ``event``, ``file``, ``line`` and ``func``;
        the call's own fields are merged in beside them. An error
        record adds ``traceback``, the ``traceback.format_exception``
        list of lines. ``json.dumps`` escapes a newline inside a value,
        which is what keeps the file one record per line.
        """
        # Callers pass their fields under "fields" because a flat
        # `extra` cannot carry a name stdlib logging already uses --
        # "msg" and "name" both collide with LogRecord's own
        # attributes. Read both through __dict__ so the transport keys
        # never leak into the JSON.
        raw = record.__dict__
        payload: dict[str, object] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "event": raw.get("event", ""),
            "file": record.filename,
            "line": record.lineno,
            "func": record.funcName,
        }
        payload.update(raw.get("fields", {}))
        if record.exc_info is not None:
            payload["traceback"] = traceback.format_exception(*record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class Logging:
    """The structured log for one app invocation.

    One instance per launch, one file per instance. ``verbose`` gates
    only the trace methods, so the always-on launch and crash records
    are written even when the operator turned the trace off. Every
    public method calls its logger directly with ``stacklevel=2`` -- a
    single hop, so ``file``/``line``/``func`` name the app frame that
    logged rather than this wrapper.

    A failed write is handled by the stdlib handler's own error path
    and never re-raised into the app.
    """

    def __init__(self, path: Path, *, verbose: bool = True) -> None:
        """Open *path* as this invocation's log.

        Args:
            path: The NDJSON file to write, named per launch by
                :func:`build_log_path`. A missing parent directory is
                created -- a first launch has none yet.
            verbose: The starting trace state, seeded from
                ``AppSettings.verbose_logging``; :meth:`set_verbose`
                toggles it live.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        # No delay and mode "w": the file exists from launch and holds
        # this invocation only, never the previous run's records.
        handler = logging.FileHandler(path, encoding="utf-8", mode="w")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(_NdjsonFormatter())

        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.DEBUG)
        # Records belong to this file only; the root logger's handlers
        # (a test run's own) must never see them.
        logger.propagate = False
        for previous in list(logger.handlers):
            logger.removeHandler(previous)
            previous.close()
        logger.addHandler(handler)
        self._logger = logger
        self._handler = handler
        self._verbose = verbose

    @property
    def verbose(self) -> bool:
        """Whether the trace methods are currently written."""
        return self._verbose

    def set_verbose(self, verbose: bool) -> None:  # noqa: FBT001 -- single unambiguous flag
        """Turn the trace records on or off (the settings toggle).

        Args:
            verbose: ``True`` writes the trace methods, ``False`` stops
                them. The always-on records ignore it.
        """
        self._verbose = verbose

    def close(self) -> None:
        """Flush and close this invocation's file handler."""
        self._handler.flush()
        self._handler.close()
        self._logger.removeHandler(self._handler)

    # --- always-on records ------------------------------------------

    def startup(  # noqa: PLR0913 -- the app_start record's documented field set
        self,
        *,
        verbose: bool,
        started_at: datetime,
        version: str,
        platform: str,
        python: str,
        pid: int,
    ) -> None:
        """Record the launch context.

        Args:
            verbose: The trace setting this launch started with.
            started_at: When the app started.
            version: The app version.
            platform: The OS, e.g. ``"darwin"``.
            python: The interpreter version.
            pid: The process id.
        """
        fields: dict[str, object] = {
            "verbose": verbose,
            "started_at": started_at.isoformat(),
            "version": version,
            "platform": platform,
            "python": python,
            "pid": pid,
        }
        self._logger.info(
            "app_start",
            extra={"event": "app_start", "fields": fields},
            stacklevel=2,
        )

    def launch(self, *, previous_state: str, previous_ride_id: int | None, choice: str) -> None:
        """Record the start/resume choice made at launch.

        Args:
            previous_state: The state the app found on disk, e.g.
                ``"no_ride"``.
            previous_ride_id: The ride being resumed, or ``None``.
            choice: What the operator chose, e.g. ``"resume"``.
        """
        fields: dict[str, object] = {
            "previous_state": previous_state,
            "previous_ride_id": previous_ride_id,
            "choice": choice,
        }
        self._logger.info(
            "launch",
            extra={"event": "launch", "fields": fields},
            stacklevel=2,
        )

    def ride_loaded(self, *, ride_id: int) -> None:
        """Record that a ride was loaded.

        Args:
            ride_id: The loaded ride's id.
        """
        self._logger.info(
            "ride_loaded",
            extra={"event": "ride_loaded", "fields": {"ride_id": ride_id}},
            stacklevel=2,
        )

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
        fields: dict[str, object] = {
            "exc_type": exc_type.__name__,
            "exc_message": str(exc_value),
        }
        self._logger.error(
            "exception",
            extra={"event": "exception", "fields": fields},
            exc_info=(exc_type, exc_value, exc_tb),
            stacklevel=2,
        )

    # --- trace records (written only when verbose) ------------------

    def marker(self, msg: str) -> None:
        """Record a free-form trace marker.

        Args:
            msg: The message, e.g. ``"ride opened"``.
        """
        if not self._verbose:
            return
        self._logger.debug(
            "marker",
            extra={"event": "marker", "fields": {"msg": msg}},
            stacklevel=2,
        )

    def menu(self, item_id: int, menu: str, label: str) -> None:
        """Record a §15 menu selection.

        Args:
            item_id: The wx menu item's event id.
            menu: The menu's own name, e.g. ``"File"``.
            label: The item's visible label.
        """
        if not self._verbose:
            return
        fields: dict[str, object] = {
            "item_id": item_id,
            "menu": menu,
            "label": label,
        }
        self._logger.debug(
            "menu",
            extra={"event": "menu", "fields": fields},
            stacklevel=2,
        )

    def dialog(self, name: str, opener: str) -> None:
        """Record a dialog opening.

        Args:
            name: The dialog's frozen XRC name.
            opener: What opened it -- the menu item or control name.
        """
        if not self._verbose:
            return
        self._logger.debug(
            "dialog",
            extra={"event": "dialog", "fields": {"name": name, "opener": opener}},
            stacklevel=2,
        )

    def button(self, name: str, label: str) -> None:
        """Record a button activation.

        Args:
            name: The button's frozen XRC name.
            label: The button's visible label.
        """
        if not self._verbose:
            return
        self._logger.debug(
            "button",
            extra={"event": "button", "fields": {"name": name, "label": label}},
            stacklevel=2,
        )

    def control(self, name: str, kind: str) -> None:
        """Record a control the app built code-side.

        Args:
            name: The frozen name applied with ``SetName``.
            kind: The control's class name, e.g. ``"TextCtrl"``.
        """
        if not self._verbose:
            return
        self._logger.debug(
            "control",
            extra={"event": "control", "fields": {"name": name, "kind": kind}},
            stacklevel=2,
        )
