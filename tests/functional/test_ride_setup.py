# SPDX-License-Identifier: GPL-3.0-only
"""Functional tests for ride_setup_dlg live (E3.5.2): real Roster.

Drives the actual ``ride_setup_dlg`` XRC dialog wired to
``rivercrossing.ui.views.ride_setup.RideSetup``, which holds a real,
in-memory :class:`~rivercrossing.roster.Roster` through
:class:`~rivercrossing.ui.presenters.setup.SetupPresenter` (E3.5.1) --
``test_rider_editor.py``'s own ``_show``/try-finally pattern, never a
mock in place of the real Roster or the real dialog.

date_picker/start_time_picker are driven with real ``wx.DateTime``
values via ``SetValue`` -- unlike the entry-mode radios, nothing in
this dialog reacts live to a date/time change, so no event injection
is needed to prove the OK handler reads them correctly (module
docstring's own "resist injection" carve-out turns out not to apply
here; it would only bite a *live* date/time reaction, which this
dialog has none of).
"""

import re
from typing import Any

import harness
import pytest
import wx

from rivercrossing.ride import RideStatus
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.ui import ids
from rivercrossing.ui.views.ride_setup import SETUP_INFOBAR, RideSetup

pytestmark = pytest.mark.functional


def _show(xrc_resource: Any, roster: Roster) -> tuple[Any, RideSetup]:  # noqa: ANN401
    """Load ride_setup_dlg, wire it live over *roster*, show, pump."""
    dialog = harness.load_window(xrc_resource, ids.RIDE_SETUP_DLG, frame=False)
    view = RideSetup(dialog, roster=roster)
    dialog.Show()
    harness.pump()
    return dialog, view


def _solo_roster() -> Roster:
    """Return a bare, solo-only DRAFT roster (Roster's own default)."""
    return Roster()


def _mixed_pooled_roster() -> Roster:
    """Return a mixed, rider_pooled, size-6 DRAFT roster."""
    return Roster(entry_mode=EntryMode.MIXED, max_team_size=6, plate_model=PlateModel.RIDER_POOLED)


def _mixed_relay_running_roster() -> Roster:
    """Return a mixed, team_relay roster already RUNNING (R-17 lock)."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.TEAM_RELAY)
    roster.status = RideStatus.RUNNING
    return roster


# ------------------------------------------------------------- opening


def test_ride_setup_dlg_infobar_disables_show_hide_effects(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Measured: same sibling pin as rider_editor_dlg's own InfoBar."""
    dialog, _view = _show(xrc_resource, _mixed_pooled_roster())

    try:
        bar = harness.find_control(dialog, SETUP_INFOBAR)
        effects = (bar.GetShowEffect(), bar.GetHideEffect())
    finally:
        harness.close_window(dialog)

    assert effects == (wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)


def test_ride_setup_dlg_opens_showing_the_presenter_supplied_deck_count(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """decks_spin has no XRC value; the presenter supplies 8 (§4)."""
    dialog, _view = _show(xrc_resource, _mixed_pooled_roster())

    try:
        deck_count = harness.find_control(dialog, ids.DECKS_SPIN).GetValue()
    finally:
        harness.close_window(dialog)

    assert deck_count == 8


def test_ride_setup_dlg_opens_showing_the_rosters_own_entry_settings(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Opening setup on a live roster shows ITS values, not XRC's."""
    dialog, _view = _show(xrc_resource, _mixed_pooled_roster())

    try:
        mixed_checked = harness.find_control(dialog, ids.MIXED_RADIO).GetValue()
        team_size = harness.find_control(dialog, ids.TEAM_SIZE_SPIN).GetValue()
        pooled_checked = harness.find_control(dialog, ids.POOLED_RADIO).GetValue()
    finally:
        harness.close_window(dialog)

    assert (mixed_checked, team_size, pooled_checked) == (True, 6, True)


def test_ride_setup_dlg_opens_with_team_fields_disabled_for_a_solo_roster(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A solo-only roster starts with team_size_spin/relay_radio off."""
    dialog, _view = _show(xrc_resource, _solo_roster())

    try:
        team_size_enabled = harness.find_control(dialog, ids.TEAM_SIZE_SPIN).IsEnabled()
        relay_enabled = harness.find_control(dialog, ids.RELAY_RADIO).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert (team_size_enabled, relay_enabled) == (False, False)


def test_ride_setup_dlg_opens_with_the_entry_group_unlocked_in_draft(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """DRAFT never locks the entry/plate-model group (R-17)."""
    dialog, _view = _show(xrc_resource, _mixed_pooled_roster())

    try:
        solo_enabled = harness.find_control(dialog, ids.SOLO_RADIO).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert solo_enabled is True


def test_ride_setup_dlg_opens_with_the_entry_group_locked_post_start_relay(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """R-17: a running relay ride locks the whole entry/plate group."""
    dialog, _view = _show(xrc_resource, _mixed_relay_running_roster())

    try:
        controls = (ids.SOLO_RADIO, ids.MIXED_RADIO, ids.TEAM_SIZE_SPIN, ids.POOLED_RADIO)
        enabled = tuple(harness.find_control(dialog, name).IsEnabled() for name in controls)
    finally:
        harness.close_window(dialog)

    assert enabled == (False, False, False, False)


# ------------------------------------------------------- live reactions


def test_ride_setup_dlg_mixed_radio_click_enables_team_fields(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Clicking mixed_radio enables team_size_spin/relay_radio live."""
    dialog, _view = _show(xrc_resource, _solo_roster())

    try:
        harness.select_radio(dialog, ids.MIXED_RADIO)
        team_size_enabled = harness.find_control(dialog, ids.TEAM_SIZE_SPIN).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert team_size_enabled is True


def test_ride_setup_dlg_solo_radio_click_disables_team_fields(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """Clicking solo_radio disables team_size_spin/relay_radio live."""
    dialog, _view = _show(xrc_resource, _mixed_pooled_roster())

    try:
        harness.select_radio(dialog, ids.SOLO_RADIO)
        relay_enabled = harness.find_control(dialog, ids.RELAY_RADIO).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert relay_enabled is False


def test_ride_setup_dlg_cap_chk_ticked_enables_cap_spin(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """cap_chk gates cap_spin's own enabled state."""
    dialog, _view = _show(xrc_resource, _mixed_pooled_roster())

    try:
        cap_chk = harness.find_control(dialog, ids.CAP_CHK)
        cap_chk.SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
        event = wx.CommandEvent(wx.EVT_CHECKBOX.typeId, cap_chk.GetId())
        event.SetEventObject(cap_chk)
        cap_chk.GetEventHandler().ProcessEvent(event)
        harness.pump()
        cap_spin_enabled = harness.find_control(dialog, ids.CAP_SPIN).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert cap_spin_enabled is True


def test_ride_setup_dlg_cap_chk_starts_unticked_with_cap_spin_disabled(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """cap_chk's own unticked default disables cap_spin at open."""
    dialog, _view = _show(xrc_resource, _mixed_pooled_roster())

    try:
        cap_spin_enabled = harness.find_control(dialog, ids.CAP_SPIN).IsEnabled()
    finally:
        harness.close_window(dialog)

    assert cap_spin_enabled is False


# ---------------------------------------------------------------- OK


def test_ride_setup_dlg_ok_with_valid_values_yields_the_built_config(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """wxID_OK with a fully valid form builds the exact RideConfig."""
    dialog, view = _show(xrc_resource, _mixed_pooled_roster())

    harness.type_text(dialog, ids.NAME_INPUT, "GORBA EPIC 2026")
    harness.type_text(dialog, ids.VENUE_INPUT, "Sea to Sky Gondola")
    harness.type_text(dialog, ids.ORGANIZER_INPUT, "GORBA")
    harness.type_text(dialog, ids.SCORER_INPUT, "K. Singh")
    harness.type_text(dialog, ids.DURATION_INPUT, "6:00")
    harness.type_text(dialog, ids.MIN_LAP_INPUT, "18:00")
    picked_date = wx.DateTime()
    picked_date.Set(20, 8, 2026)  # day, month (0-based: 8 == Sep), year
    view.date_picker.SetValue(picked_date)
    picked_time = wx.DateTime()
    picked_time.SetHMS(10, 0, 0)
    view.start_time_picker.SetValue(picked_time)

    try:
        harness.click(dialog, "wxID_OK")
        config = view.config
        shown_after = dialog.IsShown()
    finally:
        harness.close_window(dialog)

    assert config is not None
    assert (
        config.name,
        config.venue,
        config.organizer,
        config.scorer,
        config.planned_duration_s,
        config.min_lap_s,
        config.entry_mode,
        config.max_team_size,
        config.plate_model,
        config.deck_count,
    ) == (
        "GORBA EPIC 2026",
        "Sea to Sky Gondola",
        "GORBA",
        "K. Singh",
        21600,
        1080,
        EntryMode.MIXED,
        6,
        PlateModel.RIDER_POOLED,
        8,
    )
    assert shown_after is False


def test_ride_setup_dlg_ok_given_a_malformed_duration_leaves_the_dialog_open(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A refused submit shows why and never closes (mirrors E3.4)."""
    dialog, view = _show(xrc_resource, _mixed_pooled_roster())
    harness.type_text(dialog, ids.DURATION_INPUT, "not-a-duration")
    harness.type_text(dialog, ids.MIN_LAP_INPUT, "18:00")

    try:
        harness.click(dialog, "wxID_OK")
        shown_after = dialog.IsShown()
        infobar_shown = harness.find_control(dialog, SETUP_INFOBAR).IsShown()
        config_after = view.config
    finally:
        harness.close_window(dialog)

    assert shown_after is True
    assert infobar_shown is True
    assert config_after is None


def test_ride_setup_dlg_ok_given_a_zero_duration_leaves_the_dialog_open(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A RideConfig-level refusal also leaves the dialog open (§2/§6).

    ``team_size_spin``'s own XRC range (2..10) clamps any attempt to
    set it below 2, so that control alone can never reach RideConfig's
    own out-of-range check through this dialog -- duration_input has
    no such control-level floor, so "0:00" is what actually reaches
    :class:`~rivercrossing.ride.RideConfig`'s own ``planned_duration_s``
    validation end to end.
    """
    dialog, view = _show(xrc_resource, _mixed_pooled_roster())
    harness.type_text(dialog, ids.DURATION_INPUT, "0:00")
    harness.type_text(dialog, ids.MIN_LAP_INPUT, "18:00")

    try:
        harness.click(dialog, "wxID_OK")
        shown_after = dialog.IsShown()
        config_after = view.config
    finally:
        harness.close_window(dialog)

    assert shown_after is True
    assert config_after is None


# ------------------------------------------------------ tie-break list


def test_ride_setup_dlg_tiebreak_list_starts_with_the_spec_default_order(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """R-14's own order: laps, then total time, then high-card draw."""
    dialog, _view = _show(xrc_resource, _mixed_pooled_roster())

    try:
        rows = tuple(harness.find_control(dialog, ids.TIEBREAK_LIST).GetStrings())
    finally:
        harness.close_window(dialog)

    assert rows == ("Most laps", "Total time", "High-card draw")


def test_ride_setup_dlg_ok_reads_tiebreak_lists_current_order(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """A reordered tiebreak_list submits in its own current order."""
    dialog, view = _show(xrc_resource, _mixed_pooled_roster())
    harness.type_text(dialog, ids.DURATION_INPUT, "6:00")
    harness.type_text(dialog, ids.MIN_LAP_INPUT, "18:00")
    view.tiebreak_list.SetStrings(["Total time", "Most laps", "High-card draw"])

    try:
        harness.click(dialog, "wxID_OK")
        config = view.config
    finally:
        harness.close_window(dialog)

    assert config is not None
    assert config.tiebreak_order == ("total_time", "laps", "high_card")


def test_ride_setup_dlg_find_given_an_unknown_control_name_raises_naming_it(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
) -> None:
    """T-5: the one ``raise`` in ``ui.views._support.find_control``."""
    dialog, view = _show(xrc_resource, _mixed_pooled_roster())

    try:
        with pytest.raises(LookupError, match=re.escape("no control named 'no_such_control'")):
            view._find("no_such_control")
    finally:
        harness.close_window(dialog)


# ------------------------------- Fault A: the load-construct seam
# (hosted-runner red, deterministic here: a post-load step is forced
# to raise between the load and the caller's try/finally, and the
# just-loaded dialog must not be left fully alive -- see _show's guard.)


def test_show_closes_the_dialog_when_a_post_load_step_raises(
    xrc_resource: Any,  # noqa: ANN401 -- wx ships no stubs
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fault A red: a failure between load and try must not leak.

    ``_show`` loads, wires and pumps *before* the test's own
    ``try/finally``; under hosted-runner load any step in that window
    can raise (``ui.views._support.find_control``'s 25-retry
    exhaustion ``LookupError``, for one) and the just-loaded dialog
    then leaks fully alive, is rerun-masked by ``--reruns 2``, and
    later trips the reap pin. Pump is forced to raise here so the leak
    is reproduced deterministically: red until ``_show`` closes the
    dialog on the way out.
    """
    roster = _mixed_pooled_roster()

    def _pump_that_raises() -> None:
        raise LookupError("simulated post-load failure")

    monkeypatch.setattr(harness, "pump", _pump_that_raises)

    with pytest.raises(LookupError, match=re.escape("simulated post-load failure")):
        _show(xrc_resource, roster)

    assert wx.Window.FindWindowByName(ids.RIDE_SETUP_DLG) is None
