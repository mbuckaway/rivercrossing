# SPDX-License-Identifier: GPL-3.0-only
"""Headless pins for ride_setup_dlg's tie-break box, logo, jokers, cap.

The Cards box now carries ``tiebreak_list``'s own three-row box beside
the logo column (``logo_preview_bmp`` / ``logo_status_lbl`` /
``logo_browse_btn``); the standalone ``logo_picker`` row is gone. The
list's seed is :data:`~rivercrossing.ride.DEFAULT_TIEBREAK_ORDER` --
Phase 3's stored default, high-card draw first -- and a staged logo is
what ``_form_values`` submits as ``logo_path``. Phase 5 re-shaped the
jokers/card controls: the ``jokers_choice`` dropdown over 0..4 became
``jokers_spin`` (0..10) plus the ``jokers_per_deck_radio`` /
``jokers_total_radio`` pair, and the ``cap_chk`` + ``cap_spin`` pair
became one ``cap_choice`` ("Disabled" then "5".."20").

A real ``wx.Dialog`` needs a desktop, so these tests drive control
doubles and call the steps directly -- the same ``object.__new__``
stand-in ``tests/unit/ui/test_team_editor_dialog_size.py`` uses for its
own sizing step. Decoding a PNG needs a live ``wx.App``, so the staging
and preview pins live in ``test_ride_setup_logo_wx.py``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.ride import (
    DEFAULT_TIEBREAK_ORDER,
    JOKERS_MODE_PER_DECK,
    JOKERS_MODE_TOTAL,
)
from rivercrossing.ui import ids
from rivercrossing.ui.views.ride_setup import (
    _TIEBREAK_LABELS,
    CAP_DISABLED,
    TIEBREAK_LIST_MIN_SIZE,
    TIEBREAK_LIST_ROWS,
    RideSetup,
)

if TYPE_CHECKING:
    from pathlib import Path

    import wx

# The three criteria R-14 names, in Phase 3's stored default order.
DEFAULT_TIEBREAK_ROWS = ("High-card draw", "Most laps", "Total time")


class _FakeControl:
    """A child-control double: the getters/setters the view calls."""

    def __init__(self, value: object = 1, strings: list[str] | None = None) -> None:
        """Answer GetValue/GetStrings from the stored fields."""
        self._value = value
        self._strings = list(strings) if strings is not None else []
        self.min_size: wx.Size | None = None
        self.bitmap: object = None
        self.label = ""
        self.enabled = True

    def GetValue(self) -> object:  # noqa: N802 -- wx API name the SUT calls
        """Return the value this double was built with."""
        return self._value

    def SetValue(self, value: object) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the value the view applied (count or radio state)."""
        self._value = value

    def GetStrings(self) -> list[str]:  # noqa: N802 -- wx API name the SUT calls
        """Return the rows this double holds."""
        return list(self._strings)

    def SetStrings(self, strings: list[str]) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Replace the rows this double holds."""
        self._strings = list(strings)

    def GetStringSelection(self) -> str:  # noqa: N802 -- wx API name the SUT calls
        """Return the selected item's own text, as a wxChoice does."""
        return str(self._value)

    def SetStringSelection(self, value: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the item text the view selected."""
        self._value = value

    def Enable(self, enabled: bool) -> None:  # noqa: N802, FBT001 -- wx API name and positional bool
        """Record the enabled state the view applied."""
        self.enabled = enabled

    def SetMinSize(self, size: wx.Size) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the floor the view applied."""
        self.min_size = size

    def SetBitmap(self, bitmap: object) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the bitmap the view rendered."""
        self.bitmap = bitmap

    def SetLabel(self, label: str) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Record the label the view rendered."""
        self.label = label

    def GetLabel(self) -> str:  # noqa: N802 -- wx API name the SUT calls
        """Return the label this double holds."""
        return self.label


class _FakeDateTime:
    """A ``wx.DateTime`` double for the two picker controls."""

    def GetValue(self) -> _FakeDateTime:  # noqa: N802 -- wx API name the SUT calls
        """Return this double, as a wx picker returns its own date."""
        return self

    def GetYear(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Return the fake's own year."""
        return 2026

    def GetMonth(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Return wx's 0-based month (September)."""
        return 8

    def GetDay(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Return the fake's own day."""
        return 20

    def GetHour(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Return the fake's own hour."""
        return 10

    def GetMinute(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Return the fake's own minute."""
        return 0

    def GetSecond(self) -> int:  # noqa: N802 -- wx API name the SUT calls
        """Return the fake's own second."""
        return 0


class _FakeDialog:
    """The dialog double the view's ``Layout()`` calls land on."""

    def Layout(self) -> None:  # noqa: N802 -- wx API name the SUT calls
        """Accept the layout request."""


# Every control _form_values reads verbatim; the pickers, tiebreak_list
# and the three logo controls are wired separately below.
_FORM_VALUE_CONTROLS = (
    "venue_input",
    "organizer_input",
    "scorer_input",
    "duration_input",
    "min_lap_input",
    "lap_km_spin",
    "hold_short_radio",
    "mixed_radio",
    "team_size_spin",
    "relay_radio",
    "decks_spin",
    "jokers_spin",
    "jokers_per_deck_radio",
    "jokers_total_radio",
    "cap_choice",
)

NAME_INPUT_VALUE = "GORBA EPIC 2026"

# The structural-gate controls _form_values never reads (the gate
# still has to enable/disable them).
_STRUCTURE_ONLY_CONTROLS = ("solo_radio", "pooled_radio")


def _bare_view(*, logo_path: Path | None = None) -> RideSetup:
    """Return a ``RideSetup`` over control doubles, no window loaded.

    ``__init__`` resolves every frozen name and builds a real
    ``wx.InfoBar``, which needs a desktop; the tie-break and logo
    steps below read/write only their own controls, so the instance is
    made without it (``test_team_editor_dialog_size.py``'s own
    stand-in shape).
    """
    view = object.__new__(RideSetup)
    view.dialog = _FakeDialog()
    view._logo_path = logo_path
    view.tiebreak_list = _FakeControl(strings=list(_TIEBREAK_LABELS.values()))
    for name in (*_FORM_VALUE_CONTROLS, *_STRUCTURE_ONLY_CONTROLS):
        setattr(view, name, _FakeControl())
    # The doubled Cards controls carry the same XRC defaults the
    # authored controls open on (setup.xrc): 1 joker, the total radio
    # checked, the cap choice on "Disabled".
    view.jokers_spin = _FakeControl(value=1)
    view.jokers_per_deck_radio = _FakeControl(value=False)
    view.jokers_total_radio = _FakeControl(value=True)
    view.cap_choice = _FakeControl(value=CAP_DISABLED)
    view.name_input = _FakeControl(NAME_INPUT_VALUE)
    view.date_picker = _FakeDateTime()
    view.start_time_picker = _FakeDateTime()
    view.logo_preview_bmp = _FakeControl()
    view.logo_status_lbl = _FakeControl()
    return view


# ------------------------------------------------ tie-break list (3c)


def test_ride_setup_tiebreak_list_given_the_ride_seed_shows_its_three_rows() -> None:
    """R-14's criteria render in ride.DEFAULT_TIEBREAK_ORDER's order."""
    view = _bare_view()

    view.show_tiebreak_order(DEFAULT_TIEBREAK_ORDER)

    assert tuple(view.tiebreak_list.GetStrings()) == DEFAULT_TIEBREAK_ROWS


def test_ride_setup_tiebreak_list_given_the_ride_seed_holds_three_rows() -> None:
    """§3c: the seeded list is exactly TIEBREAK_LIST_ROWS rows tall."""
    view = _bare_view()

    view.show_tiebreak_order(DEFAULT_TIEBREAK_ORDER)

    assert (TIEBREAK_LIST_ROWS, len(view.tiebreak_list.GetStrings())) == (3, 3)


def test_ride_setup_tiebreak_list_declares_the_three_row_min_size() -> None:
    """§3c: a 160x120 box, the same box setup.xrc authors."""
    assert TIEBREAK_LIST_MIN_SIZE == (160, 120)


def test_ride_setup_apply_tiebreak_min_size_given_a_list_floors_it() -> None:
    """The view floors tiebreak_list at TIEBREAK_LIST_MIN_SIZE."""
    view = _bare_view()

    view._apply_tiebreak_min_size()

    floor = view.tiebreak_list.min_size
    assert (floor.width, floor.height) == TIEBREAK_LIST_MIN_SIZE


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], DEFAULT_TIEBREAK_ORDER),
        (["Most laps"], DEFAULT_TIEBREAK_ORDER),
        (["Most laps", "Total time"], DEFAULT_TIEBREAK_ORDER),
        (
            ["High-card draw", "Most laps", "Total time"],
            ("high_card", "laps", "total_time"),
        ),
        (["Most laps", "Total time", "High-card draw", "Extra"], DEFAULT_TIEBREAK_ORDER),
        (["Most laps", "Total time", "Unrecognised"], DEFAULT_TIEBREAK_ORDER),
    ],
    ids=["empty", "one_row", "two_rows", "three_rows", "four_rows", "unknown_label"],
)
def test_ride_setup_tiebreak_order_given_a_row_set_reads_it_back_as_ids(
    rows: list[str], expected: tuple[str, str, str]
) -> None:
    """The New/Delete mismatch falls back to the default (T-4 rows)."""
    view = _bare_view()
    view.tiebreak_list.SetStrings(rows)

    order = view._tiebreak_order()

    assert order == expected


@given(order=st.permutations(DEFAULT_TIEBREAK_ORDER))
def test_ride_setup_tiebreak_order_round_trips_every_permutation(
    order: list[str],
) -> None:
    """T-7 invariant: rendered rows read back as the same order."""
    view = _bare_view()

    view.show_tiebreak_order(tuple(order))

    assert view._tiebreak_order() == tuple(order)


# ---------------------------------------------------- logo column (3d)


def test_ride_setup_show_logo_given_none_shows_the_no_logo_default() -> None:
    """§3d: no staged logo reads "NO LOGO" over a blank preview."""
    view = _bare_view(logo_path=None)

    view.show_logo(None)

    assert (view.logo_status_lbl.GetLabel(), view.logo_preview_bmp.bitmap.IsOk()) == (
        "NO LOGO",
        False,
    )


def test_ride_setup_form_values_given_a_staged_logo_submits_its_path(
    tmp_path: Path,
) -> None:
    """§3d: the form submits the staged path, not picker text."""
    staged = tmp_path / "gorba-logo.png"
    view = _bare_view(logo_path=staged)

    values = view._form_values()

    assert (values.name, values.logo_path) == (NAME_INPUT_VALUE, staged)


def test_ride_setup_form_values_given_no_staged_logo_submits_none() -> None:
    """§3d nullable: no logo staged submits None (R-20)."""
    view = _bare_view(logo_path=None)

    values = view._form_values()

    assert values.logo_path is None


# --------------------------------- jokers mode + card cap (Phase 5)
# The Cards box's two shapes: jokers_spin (0..10) beside the
# per-deck/total radio pair, and the single cap_choice dropdown whose
# first item is "Disabled". The view translates the pair and the
# dropdown's item TEXT into domain values (wx has no enum control).


def test_ride_setup_ids_declare_the_phase_5_cards_controls() -> None:
    """ui/ids.py carries the four new controls' own names."""
    assert (
        ids.JOKERS_SPIN,
        ids.JOKERS_PER_DECK_RADIO,
        ids.JOKERS_TOTAL_RADIO,
        ids.CAP_CHOICE,
    ) == ("jokers_spin", "jokers_per_deck_radio", "jokers_total_radio", "cap_choice")


def test_ride_setup_ids_drop_the_retired_jokers_and_cap_controls() -> None:
    """Phase 5 retires the old jokers/cap controls from the dialog."""
    retired = [name for name in ("JOKERS_CHOICE", "CAP_CHK", "CAP_SPIN") if hasattr(ids, name)]

    assert retired == []


@pytest.mark.parametrize("count", [0, 1, 9, 10], ids=["min", "min+1", "max-1", "max"])
def test_ride_setup_form_values_given_a_jokers_spin_value_submits_it(count: int) -> None:
    """T-4: every jokers_spin value reaches the form unchanged."""
    view = _bare_view()
    view.jokers_spin.SetValue(count)

    assert view._form_values().jokers_per_deck == count


def test_ride_setup_form_values_given_the_per_deck_radio_submits_per_deck_mode() -> None:
    """jokers_per_deck_radio checked reads as the per-deck spelling."""
    view = _bare_view()
    view.jokers_per_deck_radio.SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
    view.jokers_total_radio.SetValue(False)  # noqa: FBT003 -- wx API takes a positional bool

    assert view._form_values().jokers_mode == JOKERS_MODE_PER_DECK


def test_ride_setup_form_values_given_the_total_radio_submits_total_mode() -> None:
    """jokers_total_radio checked reads as the total spelling."""
    view = _bare_view()
    view.jokers_per_deck_radio.SetValue(False)  # noqa: FBT003 -- wx API takes a positional bool
    view.jokers_total_radio.SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool

    assert view._form_values().jokers_mode == JOKERS_MODE_TOTAL


def test_ride_setup_bare_form_values_read_the_authored_jokers_defaults() -> None:
    """A fresh dialog opens on 1 joker in total mode (setup.xrc)."""
    view = _bare_view()

    values = view._form_values()

    assert (values.jokers_per_deck, values.jokers_mode) == (1, JOKERS_MODE_TOTAL)


def test_ride_setup_show_jokers_given_a_count_sets_the_spin() -> None:
    """D2 preload: the record's own joker count fills jokers_spin."""
    view = _bare_view()

    view.show_jokers(count=7, mode=JOKERS_MODE_TOTAL)

    assert view.jokers_spin.GetValue() == 7


def test_ride_setup_show_jokers_given_total_checks_the_total_radio() -> None:
    """D2 preload: the stored total mode checks the total radio."""
    view = _bare_view()

    view.show_jokers(count=2, mode=JOKERS_MODE_TOTAL)

    assert (view.jokers_total_radio.GetValue(), view.jokers_per_deck_radio.GetValue()) == (
        True,
        False,
    )


def test_ride_setup_show_jokers_given_per_deck_checks_the_per_deck_radio() -> None:
    """D2 preload: a stored per-deck mode checks the per-deck radio."""
    view = _bare_view()

    view.show_jokers(count=2, mode=JOKERS_MODE_PER_DECK)

    assert (view.jokers_total_radio.GetValue(), view.jokers_per_deck_radio.GetValue()) == (
        False,
        True,
    )


def test_ride_setup_form_values_given_the_cap_disabled_item_submits_none() -> None:
    """T-4 nullable: "Disabled" means no cap at all, not a zero."""
    view = _bare_view()
    view.cap_choice.SetStringSelection(CAP_DISABLED)

    assert view._form_values().max_cards is None


@pytest.mark.parametrize("max_cards", [5, 6, 19, 20], ids=["min", "min+1", "max-1", "max"])
def test_ride_setup_form_values_given_a_cap_item_submits_its_int(max_cards: int) -> None:
    """T-4: every numbered cap_choice item maps to its own int."""
    view = _bare_view()
    view.cap_choice.SetStringSelection(str(max_cards))

    assert view._form_values().max_cards == max_cards


def test_ride_setup_bare_form_values_read_the_authored_cap_default() -> None:
    """A fresh dialog opens cap_choice on "Disabled" (setup.xrc)."""
    view = _bare_view()

    assert view._form_values().max_cards is None


def test_ride_setup_show_max_cards_given_none_selects_the_disabled_item() -> None:
    """D2 nullable: an uncapped ride preloads "Disabled"."""
    view = _bare_view()
    view.cap_choice.SetStringSelection("20")

    view.show_max_cards(None)

    assert view.cap_choice.GetStringSelection() == CAP_DISABLED


@pytest.mark.parametrize("max_cards", [5, 20], ids=["min", "max"])
def test_ride_setup_show_max_cards_given_a_number_selects_its_item(max_cards: int) -> None:
    """D2 preload: a stored cap selects its own dropdown item."""
    view = _bare_view()

    view.show_max_cards(max_cards)

    assert view.cap_choice.GetStringSelection() == str(max_cards)


@pytest.mark.parametrize(
    "control_name",
    ["jokers_spin", "jokers_per_deck_radio", "jokers_total_radio", "cap_choice"],
)
def test_ride_setup_set_structure_enabled_given_false_disables_the_cards_controls(
    control_name: str,
) -> None:
    """D2: a started ride's jokers/cap controls are read-only."""
    view = _bare_view()

    view.set_structure_enabled(enabled=False)

    assert getattr(view, control_name).enabled is False


@pytest.mark.parametrize(
    "control_name",
    ["jokers_spin", "jokers_per_deck_radio", "jokers_total_radio", "cap_choice"],
)
def test_ride_setup_set_structure_enabled_given_true_enables_the_cards_controls(
    control_name: str,
) -> None:
    """A DRAFT ride keeps all four editable (T-3 both outcomes)."""
    view = _bare_view()
    getattr(view, control_name).enabled = False

    view.set_structure_enabled(enabled=True)

    assert getattr(view, control_name).enabled is True
