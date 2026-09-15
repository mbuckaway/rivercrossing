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

_ALL_FIELDS = {
    "appearance",
    "sound_on",
    "show_total_times",
    "show_lap_time",
    "zoom_percent",
    "splitter_sash",
    "window_geometry",
    "verbose_logging",
    "sim_riders",
    "sim_teams",
    "sim_solo",
    "sim_laps",
    "sim_interval",
    "avg_speed_kmh",
}

# The simulator dialog's XRC spin defaults (simulation.xrc): riders
# 175, teams 40, solo 15, laps 1, interval 45 (plan §1/§3).
_SIM_DEFAULTS = (175, 40, 15, 1, 45)

# The 90-150 zoom ladder, as the JSON-safe rung list files carry.
_ZOOM_RUNGS = list(ZOOM_LADDER)


# --- round-trip + defaults ----------------------------------------


def test_save_then_load_round_trips_every_field(tmp_path: Path) -> None:
    """Every AppSettings field round-trips through save/load."""
    path = tmp_path / "settings.json"
    original = AppSettings(
        appearance="dark",
        sound_on=False,
        show_total_times=True,
        show_lap_time=False,
        zoom_percent=140,
        splitter_sash=320,
        window_geometry=(40, 60, 1200, 800),
        verbose_logging=False,
        sim_riders=37,
        sim_teams=6,
        sim_solo=5,
        sim_laps=4,
        sim_interval=9,
    )

    save_settings(original, path)
    loaded = load_settings(path)

    assert loaded == original


def test_default_settings_hide_the_total_and_show_the_lap_time() -> None:
    """The two independent time-column defaults (Total off, Lap on)."""
    settings = default_settings()

    assert (settings.show_total_times, settings.show_lap_time) == (False, True)


def test_save_then_load_round_trips_the_two_time_column_flags(tmp_path: Path) -> None:
    """Both show flags survive a save/load round trip."""
    path = tmp_path / "settings.json"
    original = replace(default_settings(), show_total_times=True, show_lap_time=False)

    save_settings(original, path)
    loaded = load_settings(path)

    assert (loaded.show_total_times, loaded.show_lap_time) == (True, False)


def test_load_settings_missing_both_time_column_keys_uses_the_defaults(
    tmp_path: Path,
) -> None:
    """An older file with neither key seeds Total off and Lap on."""
    path = tmp_path / "settings.json"
    path.write_text('{"appearance": "dark"}', encoding="utf-8")

    loaded = load_settings(path)

    assert (loaded.show_total_times, loaded.show_lap_time) == (False, True)


def test_default_settings_sim_fields_are_the_dialog_xrc_defaults() -> None:
    """Plan §1: a first launch seeds the simulator's five spins."""
    settings = default_settings()

    assert (
        settings.sim_riders,
        settings.sim_teams,
        settings.sim_solo,
        settings.sim_laps,
        settings.sim_interval,
    ) == _SIM_DEFAULTS


def test_save_then_load_round_trips_the_sim_fields(tmp_path: Path) -> None:
    """The five simulator spin values survive a save/load round trip."""
    path = tmp_path / "settings.json"
    original = replace(
        default_settings(),
        sim_riders=37,
        sim_teams=6,
        sim_solo=5,
        sim_laps=4,
        sim_interval=9,
    )

    save_settings(original, path)
    loaded = load_settings(path)

    assert (
        loaded.sim_riders,
        loaded.sim_teams,
        loaded.sim_solo,
        loaded.sim_laps,
        loaded.sim_interval,
    ) == (37, 6, 5, 4, 9)


def test_load_settings_missing_sim_keys_falls_back_to_defaults(tmp_path: Path) -> None:
    """An older file with no sim keys seeds the XRC defaults."""
    path = tmp_path / "settings.json"
    path.write_text('{"appearance": "dark"}', encoding="utf-8")

    loaded = load_settings(path)

    assert (
        loaded.sim_riders,
        loaded.sim_teams,
        loaded.sim_solo,
        loaded.sim_laps,
        loaded.sim_interval,
    ) == _SIM_DEFAULTS


# --- average rider speed (plan §10) --------------------------------


def test_default_settings_defaults_avg_speed_to_twelve_kmh() -> None:
    """Plan §10: a first launch seeds the speed at 12 km/h."""
    assert default_settings().avg_speed_kmh == 12.0


def test_save_then_load_round_trips_the_avg_speed(tmp_path: Path) -> None:
    """A chosen average rider speed survives a save/load round trip."""
    path = tmp_path / "settings.json"
    original = replace(default_settings(), avg_speed_kmh=17.5)

    save_settings(original, path)
    loaded = load_settings(path)

    assert loaded.avg_speed_kmh == 17.5


def test_save_then_load_round_trips_a_fractional_avg_speed(tmp_path: Path) -> None:
    """Phase 1's decimal entry: the stored fraction survives intact."""
    path = tmp_path / "settings.json"
    original = replace(default_settings(), avg_speed_kmh=12.5)

    save_settings(original, path)
    loaded = load_settings(path)

    assert loaded.avg_speed_kmh == 12.5


def test_load_settings_missing_avg_speed_key_falls_back_to_the_default(tmp_path: Path) -> None:
    """An older file with no avg_speed_kmh key keeps the default."""
    path = tmp_path / "settings.json"
    path.write_text('{"appearance": "dark"}', encoding="utf-8")

    loaded = load_settings(path)

    assert loaded.avg_speed_kmh == 12.0


@pytest.mark.parametrize(
    ("saved_speed", "expected_speed"),
    [
        (0.5, 1.0),  # T-4: min - 1
        (1.0, 1.0),  # T-4: the floor itself
        (1.5, 1.5),  # T-4: min + 1
        (12, 12.0),  # a stored JSON int is a valid speed
        (99.0, 99.0),  # a realistic fast field
    ],
)
def test_load_settings_clamps_avg_speed_to_the_one_kmh_floor(
    tmp_path: Path, saved_speed: float, expected_speed: float
) -> None:
    """A below-floor speed rises to 1 km/h; the rest survive."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"avg_speed_kmh": saved_speed}), encoding="utf-8")

    loaded = load_settings(path)

    assert loaded.avg_speed_kmh == expected_speed


def test_load_settings_non_finite_avg_speed_uses_the_default(tmp_path: Path) -> None:
    """A JSON NaN speed is corrupt for this field: the default applies.

    ``json.loads`` accepts the bare ``NaN`` literal, and a NaN would
    survive the floor clamp and reach ``math.ceil`` -- the loader's
    never-raises contract needs it rejected here (T-3/T-4 nullable).
    """
    path = tmp_path / "settings.json"
    path.write_text('{"avg_speed_kmh": NaN}', encoding="utf-8")

    loaded = load_settings(path)

    assert loaded.avg_speed_kmh == 12.0


def test_load_settings_missing_file_returns_defaults(tmp_path: Path) -> None:
    """A path with no file is a first launch: the defaults apply."""
    loaded = load_settings(tmp_path / "no-such-settings.json")

    assert loaded == default_settings()


def test_default_settings_enable_verbose_logging() -> None:
    """F1: verbose logging is on until the operator unticks it."""
    assert default_settings().verbose_logging is True


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
        show_total_times=False,
        show_lap_time=True,
        zoom_percent=100,
        splitter_sash=None,
        window_geometry=None,
        verbose_logging=True,
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
                "show_total_times": 1,
                "show_lap_time": "yes",
                "zoom_percent": "140",
                "splitter_sash": "320",
                "window_geometry": [1, 2],
                "verbose_logging": "yes",
                "sim_riders": "ten",
                "sim_teams": True,
                "sim_solo": 2.5,
                "sim_laps": None,
                "sim_interval": "1",
                "avg_speed_kmh": "fast",
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


def test_save_settings_writes_json_with_every_field(tmp_path: Path) -> None:
    """The file is JSON carrying every AppSettings field by name."""
    path = tmp_path / "settings.json"
    save_settings(
        AppSettings(
            appearance="light",
            sound_on=False,
            show_total_times=True,
            show_lap_time=False,
            zoom_percent=120,
            splitter_sash=250,
            window_geometry=(10, 20, 30, 40),
            verbose_logging=False,
            sim_riders=12,
            sim_teams=3,
            sim_solo=4,
            sim_laps=2,
            sim_interval=5,
            avg_speed_kmh=17.5,
        ),
        path,
    )

    raw = json.loads(path.read_text(encoding="utf-8"))

    assert set(raw) == _ALL_FIELDS
    assert raw["window_geometry"] == [10, 20, 30, 40]
    assert raw["verbose_logging"] is False
    assert raw["show_total_times"] is True
    assert raw["show_lap_time"] is False
    assert raw["sim_riders"] == 12
    assert raw["sim_interval"] == 5
    assert raw["avg_speed_kmh"] == 17.5


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
    show_total_times=st.booleans(),
    show_lap_time=st.booleans(),
    zoom_percent=st.sampled_from(ZOOM_LADDER),
    splitter_sash=st.none() | st.integers(min_value=0, max_value=5000),
    window_geometry=st.none()
    | st.tuples(
        st.integers(min_value=-5000, max_value=5000),
        st.integers(min_value=-5000, max_value=5000),
        st.integers(min_value=100, max_value=5000),
        st.integers(min_value=100, max_value=5000),
    ),
    verbose_logging=st.booleans(),
    sim_riders=st.integers(min_value=0, max_value=1000),
    sim_teams=st.integers(min_value=0, max_value=100),
    sim_solo=st.integers(min_value=0, max_value=1000),
    sim_laps=st.integers(min_value=0, max_value=1000),
    sim_interval=st.integers(min_value=0, max_value=240),
    avg_speed_kmh=st.floats(min_value=1.0, max_value=400.0, allow_nan=False, allow_infinity=False),
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
