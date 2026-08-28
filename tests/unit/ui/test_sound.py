# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for ``rivercrossing.ui.sound``'s cue player (E4.4.3).

Spec §10 wires the three bundled WAV cues (recorded/flagged/error)
behind the Settings toggle (default on) and plays them via
``wx.adv.Sound`` async, "never blocks the entry field". The player
sits behind a fake-able backend seam (spec §10's own "no audio
hardware in CI" requirement): these tests inject a fake backend and
assert *which cue fired*, never real hardware. A missing WAV must
fall back to SILENT, never raise (the bundled cue set could ship
incomplete and the console must not crash on the way to the next
crossing).
"""

from typing import TYPE_CHECKING

import pytest

from rivercrossing.ui import sound
from rivercrossing.ui.sound import Cue, SoundPlayer

if TYPE_CHECKING:
    from pathlib import Path


class FakeBackend:
    """Records every path the player asked its backend to play."""

    def __init__(self) -> None:
        """Start with an empty play log."""
        self.played: list[str] = []

    def play(self, path: Path) -> None:
        """Record *path* exactly as the real backend receives it."""
        self.played.append(str(path))


def _sounds_dir() -> Path:
    """Return the packaged ``assets/sounds/`` directory."""
    return sound.sounds_dir()


@pytest.mark.parametrize(
    ("cue", "wav"),
    [
        (Cue.RECORDED, "recorded.wav"),
        (Cue.FLAGGED, "flagged.wav"),
        (Cue.ERROR, "error.wav"),
    ],
)
def test_sound_play_given_cue_plays_its_matching_wav(cue: Cue, wav: str) -> None:
    """Each cue maps to exactly its own bundled WAV file (spec §10)."""
    fake = FakeBackend()

    SoundPlayer(backend=fake).play(cue)

    assert fake.played == [str(_sounds_dir() / wav)]


def test_sound_play_given_muted_player_plays_no_cue_at_all() -> None:
    """The Settings toggle (default on) mutes every cue when off."""
    fake = FakeBackend()
    player = SoundPlayer(backend=fake)
    player.set_muted(muted=True)

    player.play(Cue.RECORDED)
    player.play(Cue.FLAGGED)
    player.play(Cue.ERROR)

    assert fake.played == []


def test_sound_play_given_unmuted_player_plays_after_a_mute_toggle_round_trip() -> None:
    """Muting then unmuting restores playback (toggle is two-way)."""
    fake = FakeBackend()
    player = SoundPlayer(backend=fake)
    player.set_muted(muted=True)
    player.set_muted(muted=False)

    player.play(Cue.FLAGGED)

    assert fake.played == [str(_sounds_dir() / "flagged.wav")]


def test_sound_play_given_missing_wav_falls_back_silent_never_raises(tmp_path: Path) -> None:
    """Negative: a dir without the WAV plays nothing, raises nothing."""
    fake = FakeBackend()

    SoundPlayer(backend=fake, sounds_dir_path=tmp_path).play(Cue.RECORDED)

    assert fake.played == []


def test_sound_module_level_play_delegates_to_the_default_player(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sound.play`` is the module convenience over one player."""
    fake = FakeBackend()
    monkeypatch.setattr(sound, "_default_player", SoundPlayer(backend=fake))

    sound.play(Cue.ERROR)

    assert fake.played == [str(_sounds_dir() / "error.wav")]


def test_sound_module_level_set_muted_silences_the_default_player(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``sound.set_muted`` drives the default player ``play`` uses."""
    fake = FakeBackend()
    monkeypatch.setattr(sound, "_default_player", SoundPlayer(backend=fake))

    sound.set_muted(muted=True)
    sound.play(Cue.RECORDED)

    assert fake.played == []
