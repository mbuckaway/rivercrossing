# SPDX-License-Identifier: GPL-3.0-only
"""Settings presenter -- settings_dlg (3a), app-wide preferences.

Pure Python -- no ``wx`` import may ever land here (R-71).

E8.1.1 owns persistence: every app setting survives relaunch through
one per-user JSON config file (``platformdirs.user_config_dir``), NOT
the ride database -- the user's decision recorded in the task, so the
SQLite schema stays untouched. The store is plain module functions
(SIMPLECODE Rule 5 -- no state, so no class): :func:`default_path`
locates the file, :func:`load_settings` reads it (never raising),
:func:`save_settings` writes it atomically, and the dialog's
:class:`SettingsPresenter` stays wx-free like every other presenter.
"""

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, overload, runtime_checkable

from platformdirs import user_config_dir

from rivercrossing.ui.theme import ThemeMode

if TYPE_CHECKING:
    from collections.abc import Mapping

    from rivercrossing.ui.presenters.data_source import DataSource

__all__ = [
    "DEFAULT_ZOOM_PERCENT",
    "ZOOM_LADDER",
    "AppSettings",
    "SettingsPresenter",
    "SettingsView",
    "default_path",
    "default_settings",
    "load_settings",
    "save_settings",
]

# The documented zoom ladder: the same 90/100/110/120/130/140/150 rungs
# the XRC zoom_choice and the mi_zoom_* menu carry (settings.xrc,
# spec.md 13 / R-04). Only these are valid.
ZOOM_LADDER: tuple[int, ...] = (90, 100, 110, 120, 130, 140, 150)

# The menu default app.py's _check_default_menu_radios ticks.
DEFAULT_ZOOM_PERCENT = 100

# The three ThemeMode spellings, as a tuple for membership tests.
_THEME_SPELLINGS: tuple[str, ...] = tuple(mode.value for mode in ThemeMode)


@dataclass(frozen=True, slots=True)
class AppSettings:
    """The settings_dlg fields (appearance, sound, times, zoom, layout).

    E8.1.1 adds the two layout fields: ``splitter_sash`` and
    ``window_geometry`` (x, y, width, height) persist the console's
    sash position and the frame's placement -- the two settings that
    survive relaunch but have no dialog control. ``None`` means no
    saved value yet (a first launch or an older file).
    """

    appearance: str
    sound_on: bool
    hide_times: bool
    zoom_percent: int
    splitter_sash: int | None = None
    window_geometry: tuple[int, int, int, int] | None = None


def default_settings() -> AppSettings:
    """Return the all-defaults :class:`AppSettings`.

    The first-launch / corrupt-file fallback: System appearance, sound
    on (spec §10's default), times shown, 100% zoom, and no saved
    layout yet.
    """
    return AppSettings(
        appearance=ThemeMode.SYSTEM.value,
        sound_on=True,
        hide_times=False,
        zoom_percent=DEFAULT_ZOOM_PERCENT,
        splitter_sash=None,
        window_geometry=None,
    )


def default_path() -> Path:
    """Return the per-user settings file path (platformdirs, E8.1.1).

    ``platformdirs.user_config_dir("RiverCrossing")`` is the per-user
    config directory on every platform (``~/Library/Application
    Support`` on macOS, ``%LOCALAPPDATA%`` on Windows).
    """
    return Path(user_config_dir("RiverCrossing")) / "settings.json"


def load_settings(path: Path | None = None) -> AppSettings:
    """Return the persisted settings at *path*, defaults when absent.

    A missing file, undecodable JSON, or a JSON value that is not an
    object all return :func:`default_settings` -- loading never
    raises. Unknown keys, missing keys and wrong value types default
    field-by-field, and ``zoom_percent`` is clamped onto
    :data:`ZOOM_LADDER` (only the 90-150 rungs are valid).

    Args:
        path: The settings file to read; ``None`` uses
            :func:`default_path`.

    Returns:
        The loaded :class:`AppSettings`.
    """
    settings_path = path if path is not None else default_path()
    try:
        raw = json.loads(settings_path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return default_settings()
    if not isinstance(raw, dict):
        return default_settings()
    return _settings_from_mapping(raw)


def save_settings(settings: AppSettings, path: Path | None = None) -> None:
    """Persist *settings* to *path* (default :func:`default_path`).

    Creates the parent directory, writes the JSON payload to a
    temporary sibling, then atomically replaces *path* with it
    (``Path.replace``) -- a crash mid-write leaves the previous file
    intact. All six fields are written by name.

    Args:
        settings: The settings to persist.
        path: The target file; ``None`` uses :func:`default_path`.
    """
    settings_path = path if path is not None else default_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "appearance": settings.appearance,
        "sound_on": settings.sound_on,
        "hide_times": settings.hide_times,
        "zoom_percent": settings.zoom_percent,
        "splitter_sash": settings.splitter_sash,
        "window_geometry": (
            list(settings.window_geometry) if settings.window_geometry is not None else None
        ),
    }
    tmp = settings_path.with_name(settings_path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    tmp.replace(settings_path)


def _settings_from_mapping(raw: Mapping[str, object]) -> AppSettings:
    """Build an :class:`AppSettings` from a decoded JSON object.

    Each field reads through a coercer that returns the field's
    default when the stored value is missing or the wrong type.
    """
    defaults = default_settings()
    return AppSettings(
        appearance=_appearance_or(raw.get("appearance"), defaults.appearance),
        sound_on=_bool_or(raw.get("sound_on"), default=defaults.sound_on),
        hide_times=_bool_or(raw.get("hide_times"), default=defaults.hide_times),
        zoom_percent=_clamp_zoom(_int_or(raw.get("zoom_percent"), defaults.zoom_percent)),
        splitter_sash=_int_or(raw.get("splitter_sash"), None),
        window_geometry=_geometry_or(raw.get("window_geometry"), None),
    )


def _clamp_zoom(zoom: int) -> int:
    """Clamp *zoom* onto :data:`ZOOM_LADDER`.

    A value already on a rung returns unchanged; anything else moves
    to the nearest rung, with ties resolving to the lower rung
    (95 -> 90, 125 -> 120).
    """
    if zoom in ZOOM_LADDER:
        return zoom
    return min(ZOOM_LADDER, key=lambda rung: (abs(rung - zoom), rung))


def _appearance_or(value: object, default: str) -> str:
    """Return *value* when it is a ThemeMode spelling."""
    if isinstance(value, str) and value in _THEME_SPELLINGS:
        return value
    return default


def _bool_or(value: object, *, default: bool) -> bool:
    """Return *value* when it is a JSON bool, else *default*."""
    return value if isinstance(value, bool) else default


@overload
def _int_or(value: object, default: int) -> int: ...


@overload
def _int_or(value: object, default: None) -> int | None: ...


def _int_or(value: object, default: int | None) -> int | None:
    """Return *value* when it is a JSON int, never a bool."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return default


def _geometry_or(
    value: object, default: tuple[int, int, int, int] | None
) -> tuple[int, int, int, int] | None:
    """Return *value* when it is a JSON array of four ints."""
    if (
        isinstance(value, list)
        and len(value) == 4  # noqa: PLR2004 -- the four geometry components (x, y, width, height)
        and all(isinstance(item, int) and not isinstance(item, bool) for item in value)
    ):
        return (value[0], value[1], value[2], value[3])
    return default


@runtime_checkable
class SettingsView(Protocol):
    """View surface for the settings dialog (settings_dlg, 3a)."""

    def show_settings(self, settings: AppSettings) -> None:
        """Render the current appearance/sound/times/zoom values."""
        ...


class SettingsPresenter:
    """Presenter for the settings dialog (settings_dlg, 3a).

    Holds ``(view, data_source)`` like every other presenter. E8.1.1
    gives it the loading half: :meth:`load` returns the persisted
    :class:`AppSettings` (or the defaults) the dialog opens onto.
    E8.2 wires the appearance radios, sound/hide-times toggles, zoom
    choice and backup_now_btn to this view.
    """

    def __init__(self, view: SettingsView, data_source: DataSource) -> None:
        """Store the view and data source this presenter drives."""
        self.view = view
        self.data_source = data_source

    def load(self, path: Path | None = None) -> AppSettings:
        """Return the persisted settings, or defaults when none exist.

        Args:
            path: The settings file to read; ``None`` uses
                :func:`default_path`.

        Returns:
            The current :class:`AppSettings` for the dialog to render.
        """
        return load_settings(path)
