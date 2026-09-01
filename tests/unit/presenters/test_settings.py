# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for the E8.1.1 per-user settings persistence (headless).

``ui.presenters.settings`` stays wx-free (R-71), so its whole surface
-- the ``AppSettings`` dataclass, ``default_path``, ``load_settings``
and ``save_settings`` -- is testable on the host without ever
constructing a window. Every test writes to a ``tmp_path``, never the
real user config dir (E8.1.1's own rule).

The ``path is None`` defaulting branch in both ``load_settings`` and
``save_settings`` is exercised by monkeypatching the module's own
``default_path`` to a temp file: the config file is the filesystem I/O
boundary under test (T-10), and the alternative -- calling with no
path -- would read the real per-user config dir.
"""

import json
from dataclasses import replace
from pathlib import (
    Path,  # noqa: TC003 -- @given inspects signatures; these annotations run at runtime
)

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from rivercrossing.ui.presenters import settings as settings_module
from rivercrossing.ui.presenters.settings import (
    ZOOM_LADDER,
    AppSettings,
    default_path,
    default_settings,
    load_settings,
    save_settings,
)
from rivercrossing.ui.theme import ThemeMode

_SIX_FIELDS = {
    "appearance",
    "sound_on",
    "hide_times",
    "zoom_percent",
    "splitter_sash",
    "window_geometry",
}

# The 90-150 zoom ladder, as the JSON-safe rung list files carry.
_ZOOM_RUNGS = list(ZOOM_LADDER)


# --- round-trip + defaults ----------------------------------------


def test_save_then_load_round_trips_every_field(tmp_path: Path) -> None:
    """All six fields survive save_settings -> load_settings intact."""
    path = tmp_path / "settings.json"
    original = AppSettings(
        appearance="dark",
        sound_on=False,
        hide_times=True,
        zoom_percent=140,
        splitter_sash=320,
        window_geometry=(40, 60, 1200, 800),
    )

    save_settings(original, path)
    loaded = load_settings(path)

    assert loaded == original


def test_load_settings_missing_file_returns_defaults(tmp_path: Path) -> None:
    """A path with no file is a first launch: the defaults apply."""
    loaded = load_settings(tmp_path / "no-such-settings.json")

    assert loaded == default_settings()


def test_load_settings_corrupt_json_returns_defaults_without_raising(
    tmp_path: Path,
) -> None:
    """Undecodable JSON falls back to defaults; loading never raises."""
    path = tmp_path / "settings.json"
    path.write_text('{"appearance": "dark", oops', encoding="utf-8")

    loaded = load_settings(path)

    assert loaded == default_settings()


def test_load_settings_json_that_is_not_an_object_returns_defaults(
    tmp_path: Path,
) -> None:
    """A JSON array/scalar is corrupt for our purposes: defaults."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps(_ZOOM_RUNGS), encoding="utf-8")

    loaded = load_settings(path)

    assert loaded == default_settings()


def test_load_settings_missing_keys_use_defaults_for_each_field(
    tmp_path: Path,
) -> None:
    """A partial file defaults only the fields it omits."""
    path = tmp_path / "settings.json"
    path.write_text('{"appearance": "dark"}', encoding="utf-8")

    loaded = load_settings(path)

    assert loaded == AppSettings(
        appearance="dark",
        sound_on=True,
        hide_times=False,
        zoom_percent=100,
        splitter_sash=None,
        window_geometry=None,
    )


def test_load_settings_wrong_value_types_use_defaults_for_each_field(
    tmp_path: Path,
) -> None:
    """A stored value of the wrong type is corrupt for that field."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "appearance": 42,
                "sound_on": "yes",
                "hide_times": 1,
                "zoom_percent": "140",
                "splitter_sash": "320",
                "window_geometry": [1, 2],
            }
        ),
        encoding="utf-8",
    )

    loaded = load_settings(path)

    assert loaded == default_settings()


def test_load_settings_unknown_appearance_spelling_uses_system_default(
    tmp_path: Path,
) -> None:
    """Only the three ThemeMode spellings are accepted on load."""
    path = tmp_path / "settings.json"
    path.write_text('{"appearance": "neon"}', encoding="utf-8")

    loaded = load_settings(path)

    assert loaded.appearance == ThemeMode.SYSTEM.value


# --- zoom clamping (T-3/T-4) --------------------------------------


@pytest.mark.parametrize(
    ("saved_zoom", "expected_zoom"),
    [
        (0, 90),
        (85, 90),
        (95, 90),
        (105, 100),
        (125, 120),
        (149, 150),
        (160, 150),
    ],
)
def test_load_settings_clamps_zoom_percent_given_each_out_of_ladder_value(
    tmp_path: Path, saved_zoom: int, expected_zoom: int
) -> None:
    """Off-ladder zooms snap to the nearest rung (ties to the lower)."""
    path = tmp_path / "settings.json"
    save_settings(replace(default_settings(), zoom_percent=saved_zoom), path)

    loaded = load_settings(path)

    assert loaded.zoom_percent == expected_zoom


@pytest.mark.parametrize("rung", ZOOM_LADDER)
def test_load_settings_keeps_each_valid_zoom_rung_unchanged(tmp_path: Path, rung: int) -> None:
    """Every ladder rung (90-150 step 10) round-trips unchanged."""
    path = tmp_path / "settings.json"
    save_settings(replace(default_settings(), zoom_percent=rung), path)

    loaded = load_settings(path)

    assert loaded.zoom_percent == rung


# --- save behaviour ------------------------------------------------


def test_save_settings_creates_missing_parent_directories(tmp_path: Path) -> None:
    """save_settings makes the config dir before writing."""
    path = tmp_path / "a" / "b" / "settings.json"

    save_settings(default_settings(), path)

    assert load_settings(path) == default_settings()


def test_save_settings_writes_json_with_all_six_fields(tmp_path: Path) -> None:
    """The file is JSON carrying every AppSettings field by name."""
    path = tmp_path / "settings.json"
    save_settings(
        AppSettings(
            appearance="light",
            sound_on=False,
            hide_times=True,
            zoom_percent=120,
            splitter_sash=250,
            window_geometry=(10, 20, 30, 40),
        ),
        path,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))

    assert set(raw) == _SIX_FIELDS
    assert raw["window_geometry"] == [10, 20, 30, 40]


# --- default-path wiring (path=None branches) ----------------------


def test_load_settings_uses_default_path_when_none_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """path=None reads the platformdirs path (here, a tmp file)."""
    monkeypatch.setattr(settings_module, "default_path", lambda: tmp_path / "settings.json")
    save_settings(replace(default_settings(), appearance="light"), tmp_path / "settings.json")

    loaded = load_settings()

    assert loaded.appearance == "light"


def test_save_settings_uses_default_path_when_none_given(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """path=None writes the platformdirs path (here, a tmp file)."""
    monkeypatch.setattr(settings_module, "default_path", lambda: tmp_path / "settings.json")

    save_settings(default_settings())

    assert (tmp_path / "settings.json").is_file()


def test_default_path_ends_with_settings_json() -> None:
    """The per-user config path names the settings file (E8.1.1)."""
    assert str(default_path()).endswith("settings.json")


# --- appearance_for_radio: the dialog's radio -> appearance map -----
# (E8.1.2: the wx-free half of SettingsDialog.collect_settings.)

APPEARANCE_FOR_RADIO_CASES = (
    # Neither checked: the System radio's state, implied by both false
    # (also the xrc's structural default when none reads checked).
    (False, False, "system"),
    (True, False, "light"),
    (False, True, "dark"),
    # Degenerate multi-checked (a programmatic SetValue that does not
    # auto-uncheck the group): Light wins over Dark.
    (True, True, "light"),
)


@pytest.mark.parametrize(("light", "dark", "expected"), APPEARANCE_FOR_RADIO_CASES)
def test_appearance_for_radio_given_each_radio_state_returns_its_spelling(
    *, light: bool, dark: bool, expected: str
) -> None:
    """The checked appearance radio names the appearance to store."""
    result = settings_module.appearance_for_radio(light=light, dark=dark)

    assert result == expected


# --- property: exact round-trip over valid settings (T-7) ----------

_SETTINGS_STRATEGY = st.builds(
    AppSettings,
    appearance=st.sampled_from(tuple(mode.value for mode in ThemeMode)),
    sound_on=st.booleans(),
    hide_times=st.booleans(),
    zoom_percent=st.sampled_from(ZOOM_LADDER),
    splitter_sash=st.none() | st.integers(min_value=0, max_value=5000),
    window_geometry=st.none()
    | st.tuples(
        st.integers(min_value=-5000, max_value=5000),
        st.integers(min_value=-5000, max_value=5000),
        st.integers(min_value=100, max_value=5000),
        st.integers(min_value=100, max_value=5000),
    ),
)


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(_SETTINGS_STRATEGY)
def test_save_then_load_round_trips_any_valid_settings(
    tmp_path: Path, settings: AppSettings
) -> None:
    """Property: every valid AppSettings round-trips exactly (T-7).

    The suppressed health check is safe here: each generated example
    writes then reads the same ``tmp_path`` file, so no state leaks
    between inputs (the file is overwritten before every read).
    """
    path = tmp_path / "settings.json"

    save_settings(settings, path)
    loaded = load_settings(path)

    assert loaded == settings
