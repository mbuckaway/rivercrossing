# SPDX-License-Identifier: GPL-3.0-only
"""The three console audio cues, played via ``wx.adv.Sound`` (spec §10).

Spec §10's "Sound on crossing": three bundled WAV cues so the operator
never has to look down -- recorded (one short click the instant a
crossing commits), rejected (a low double-buzz for an unknown plate or
empty Enter), held (a distinct two-tone alert when a short-lap
crossing holds its card). Everything else is silent. Played via
``wx.adv.Sound`` **async** -- never blocks the entry field.

The player sits behind a fake-able backend seam (spec §10's own "no
audio hardware in CI"): :class:`SoundPlayer` takes a ``backend``
object with a ``play(path)`` method, and unit tests inject a fake that
records which cue fired instead of touching real hardware.

This module is **wx-lazy**: it imports no ``wx`` name at module scope.
``ui.presenters.console`` re-exports :class:`Cue` from here (the
ConsoleView protocol is typed against it), and the presenters package
must stay importable headless with ``wx`` absent from ``sys.modules``
(R-71). The real ``wx.adv.Sound`` import lives inside the default
backend's ``play`` call, where a live view actually needs it.

A missing WAV falls back to SILENT, never raises: the bundled cue set
could ship incomplete and the console must not crash on the way to the
next crossing.
"""

from enum import Enum
from pathlib import Path
from typing import Any

__all__ = ["Cue", "SoundPlayer", "play", "set_muted", "sounds_dir"]


class Cue(Enum):
    """Audio cue identifiers for console feedback (Spec §10)."""

    RECORDED = "recorded"
    FLAGGED = "flagged"
    ERROR = "error"


# One bundled WAV per cue (spec §10 / sound-cues.md). ``Cue.value``
# doubles as the filename stem, so a dict is the single mapping.
_CUE_WAV_NAMES: dict[Cue, str] = {
    Cue.RECORDED: "recorded.wav",
    Cue.FLAGGED: "flagged.wav",
    Cue.ERROR: "error.wav",
}


def sounds_dir() -> Path:
    """Return the packaged ``ui/assets/sounds/`` directory."""
    return Path(__file__).resolve().parent / "assets" / "sounds"


class _WxSoundBackend:
    """The real backend: ``wx.adv.Sound`` async playback (spec §10).

    Imported lazily so this module -- and anything that imports only
    :class:`Cue` from it -- never drags ``wx`` into a headless
    process (module docstring). ``wx.adv.Sound`` on a path it cannot
    decode reports ``IsOk()`` False and is simply not played; that
    check, plus the caller's own missing-file guard, is what keeps a
    broken or absent cue SILENT rather than a crash.
    """

    def play(self, path: Path) -> None:
        """Play *path* with ``wx.adv.Sound`` in async mode.

        # logic-coverage-exempt: T-3 -- the ``IsOk()`` guard's False
        # arm is third-party glue on a real audio stack that CI
        # deliberately never drives (spec §10 "no audio hardware in
        # CI"); unit tests inject a fake backend, and the functional
        # suite plays real WAVs through this path on hosts with audio.
        """
        import wx.adv  # noqa: PLC0415 -- lazy; see module docstring

        sound = wx.adv.Sound(str(path))
        if sound.IsOk():
            sound.Play(wx.adv.SOUND_ASYNC)


class SoundPlayer:
    """The Settings-gated cue player (spec §10: toggle, default on).

    ``muted`` defaults to False (the Settings toggle's "default on").
    :meth:`play` resolves the cue's bundled WAV, silently does nothing
    when muted or when the WAV is missing, and otherwise hands the
    path to the backend -- the one fake-able I/O boundary (T-10). The
    backend is duck-typed (a ``play(path)`` method): the real
    ``wx.adv.Sound`` backend, or a test fake that records the path.
    """

    def __init__(
        self,
        *,
        backend: Any | None = None,  # noqa: ANN401 -- duck-typed seam (T-10 I/O boundary)
        sounds_dir_path: Path | None = None,
    ) -> None:
        """Build over *backend*, reading cues from *sounds_dir_path*.

        Args:
            backend: The playback seam; defaults to the real
                ``wx.adv.Sound`` backend.
            sounds_dir_path: Where to look for the WAV files; defaults
                to the packaged ``assets/sounds/`` dir. Tests point
                this at a tmp dir to exercise the missing-WAV
                fallback.
        """
        self._backend: Any = backend if backend is not None else _WxSoundBackend()
        self._sounds_dir = sounds_dir_path if sounds_dir_path is not None else sounds_dir()
        self._muted = False

    def set_muted(self, *, muted: bool) -> None:
        """Set the Settings toggle: muted on/off (default on)."""
        self._muted = muted

    def play(self, cue: Cue) -> None:
        """Play *cue*'s WAV, silently skipping when muted or missing."""
        if self._muted:
            return
        path = self._sounds_dir / _CUE_WAV_NAMES[cue]
        if not path.is_file():
            return  # missing cue = SILENT, never raises (spec §10)
        self._backend.play(path)


# The one default player the views' ``sound.play(cue)`` convenience
# drives; the Settings toggle (E8) flips it through :func:`set_muted`.
_default_player = SoundPlayer()


def play(cue: Cue) -> None:
    """Play *cue* through the default player (module-skeletons S4)."""
    _default_player.play(cue)


def set_muted(*, muted: bool) -> None:
    """Toggle the default player's mute (spec §10's Settings toggle)."""
    _default_player.set_muted(muted=muted)
