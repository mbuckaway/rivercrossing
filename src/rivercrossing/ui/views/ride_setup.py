# SPDX-License-Identifier: GPL-3.0-only
"""``RideSetup``: ride_setup_dlg live (1c/7a, E3.5.2), on a real Roster.

E3.5.1 gave :class:`~rivercrossing.ui.presenters.setup.SetupPresenter`
real logic over a live :class:`~rivercrossing.roster.Roster`; this
module is its view half, mirroring ``rider_editor.py``'s own
constructor shape (``roster=`` rather than ``data_source=``) and
``rider_editor.py``'s code-side ``wx.InfoBar`` pattern for
:data:`SETUP_INFOBAR` (measured hang otherwise -- see
:meth:`RideSetup._build_infobar`'s docstring).

Code-side per xrc-windows.md's own footnote: field values are loaded
from the ride record (setup.xrc's own header repeats this); the
entry-mode and plate-model groups lock after start for relay rides
and stay editable for pooled ones (R-17); ``tiebreak_list``'s rows
and their reorder are persisted. A submit whose built config fails
the minimum-setup rule (blank name/venue/organizer/scorer or a non-
positive lap length) is refused like any other -- the dialog stays
open on :data:`SETUP_INFOBAR`, and ``on_submitted`` never fires
(``SetupPresenter.on_submit``'s own docstring). ``tiebreak_list`` (a
``wx.adv.EditableListBox``) carries no XRC rows at all -- this module
seeds it with R-14's own three named criteria, in the mock's default
order, as **plain** labels ("Most laps", not "① Most laps"): a
baked-in rank prefix would go stale the instant the operator uses the
control's own Up/Down buttons to reorder it, defeating the point of a
reorderable list (this task's own doc-silence -- xrc-windows.md's
mock draws the numbering as static illustration, not literal row
text).

``tiebreak_list`` also ships generic New/Delete buttons this dialog
never disables (no XRC style suppresses them): :meth:`RideSetup.
_tiebreak_order` falls back to :data:`~rivercrossing.ride.
DEFAULT_TIEBREAK_ORDER` if the operator leaves anything other than
exactly the three known rows, rather than crash on an unrecognised
label -- this task's own scope is the *reorder* case ("reorder
persisted", not "row set editable"), and a New/Delete-caused mismatch
is flagged here as a known, undefended gap for follow-up, not fixed
outright.
"""

from datetime import date, time
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import wx
import wx.adv

from rivercrossing.ride import (
    DEFAULT_JOKERS_PER_DECK,
    DEFAULT_TIEBREAK_ORDER,
    TIEBREAK_HIGH_CARD,
    TIEBREAK_LAPS,
)
from rivercrossing.ride import TIEBREAK_TOTAL_TIME as _TIEBREAK_TOTAL_TIME
from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.ui import ids
from rivercrossing.ui.presenters.setup import (
    SetupFormValues,
    SetupPresenter,
    _format_duration,
    _format_min_lap,
)
from rivercrossing.ui.views._support import find_control

if TYPE_CHECKING:
    from collections.abc import Callable

    from rivercrossing.ride import RideConfig
    from rivercrossing.roster import Roster

__all__ = ["SETUP_INFOBAR", "RideSetup"]

# ui/ids.py is generated from the .xrc files (R-05); this name never
# appears there since XRC cannot author a wxInfoBar at all
# (xrc-windows.md's own code-side footnote, rider_editor.py's
# precedent for ROSTER_INFOBAR/CSV_INFOBAR).
SETUP_INFOBAR = "setup_infobar"

# tiebreak_list's own plain-label seed (module docstring) -- the
# labels a fresh dialog shows, and the ones _tiebreak_order() maps
# back onto rivercrossing.ride's own tiebreak identifiers.
_TIEBREAK_LABELS: dict[str, str] = {
    TIEBREAK_LAPS: "Most laps",
    _TIEBREAK_TOTAL_TIME: "Total time",
    TIEBREAK_HIGH_CARD: "High-card draw",
}
_TIEBREAK_IDS_BY_LABEL: dict[str, str] = {label: id_ for id_, label in _TIEBREAK_LABELS.items()}

# The jokers radio group's third choice (setup.xrc's jokers_0/2/4_radio
# trio); 2, the group's XRC default, is ride.py's own
# DEFAULT_JOKERS_PER_DECK, so only the odd one out needs a name here.
JOKERS_4_PER_DECK = 4


class RideSetup:
    """Code-side behaviour for ``ride_setup_dlg`` (1c/7a, R-17)."""

    def __init__(  # noqa: PLR0913 -- (dialog, roster, config, on_submitted): the view's own wiring
        self,
        dialog: wx.Dialog,
        *,
        roster: Roster,
        config: RideConfig | None = None,
        on_submitted: Callable[[RideConfig], None] | None = None,
    ) -> None:
        """Decorate an already-loaded ``ride_setup_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``setup.xrc``.
            roster: The in-memory roster whose own entry_mode/
                max_team_size/plate_model/status this dialog reads
                (``SetupPresenter``'s own module docstring).
            config: The ride being edited (D2's Edit Ride…), or
                ``None`` for a New Ride; when given, every field opens
                on that ride's own values and the name/date/start/
                venue/organizer/scorer fields stay editable while the
                ride-shape controls are gated to DRAFT.
            on_submitted: A callback invoked with the built
                :class:`~rivercrossing.ride.RideConfig` when a submit
                commits (E9.1.2 -- the app wires it to
                ``Store.create_ride`` + ``Store.save_roster`` for a New
                Ride, and to ``Store.update_ride_config`` + the live
                engine for an Edit Ride); ``None`` keeps the in-memory
                behavior.
        """
        self.dialog = dialog
        self.config: RideConfig | None = None
        self.on_submitted = on_submitted

        self.name_input = self._find(ids.NAME_INPUT, wx.TextCtrl)
        self.date_picker = self._find(ids.DATE_PICKER, wx.adv.DatePickerCtrl)
        self.start_time_picker = self._find(ids.START_TIME_PICKER, wx.adv.TimePickerCtrl)
        self.venue_input = self._find(ids.VENUE_INPUT, wx.TextCtrl)
        self.lap_km_spin = self._find(ids.LAP_KM_SPIN, wx.SpinCtrlDouble)
        self.organizer_input = self._find(ids.ORGANIZER_INPUT, wx.TextCtrl)
        self.scorer_input = self._find(ids.SCORER_INPUT, wx.TextCtrl)
        self.duration_input = self._find(ids.DURATION_INPUT, wx.TextCtrl)
        self.min_lap_input = self._find(ids.MIN_LAP_INPUT, wx.TextCtrl)
        self.hold_short_radio = self._find(ids.HOLD_SHORT_RADIO, wx.RadioButton)
        self.always_deal_radio = self._find(ids.ALWAYS_DEAL_RADIO, wx.RadioButton)
        self.logo_picker = self._find(ids.LOGO_PICKER, wx.FilePickerCtrl)
        self.solo_radio = self._find(ids.SOLO_RADIO, wx.RadioButton)
        self.mixed_radio = self._find(ids.MIXED_RADIO, wx.RadioButton)
        self.team_size_spin = self._find(ids.TEAM_SIZE_SPIN, wx.SpinCtrl)
        self.pooled_radio = self._find(ids.POOLED_RADIO, wx.RadioButton)
        self.relay_radio = self._find(ids.RELAY_RADIO, wx.RadioButton)
        self.decks_spin = self._find(ids.DECKS_SPIN, wx.SpinCtrl)
        self.jokers_0_radio = self._find(ids.JOKERS_0_RADIO, wx.RadioButton)
        self.jokers_2_radio = self._find(ids.JOKERS_2_RADIO, wx.RadioButton)
        self.jokers_4_radio = self._find(ids.JOKERS_4_RADIO, wx.RadioButton)
        self.cap_chk = self._find(ids.CAP_CHK, wx.CheckBox)
        self.cap_spin = self._find(ids.CAP_SPIN, wx.SpinCtrl)
        self.tiebreak_list = self._find(ids.TIEBREAK_LIST, wx.adv.EditableListBox)
        self.ok_btn = self._find("wxID_OK", wx.Button)

        self.tiebreak_list.SetStrings([_TIEBREAK_LABELS[id_] for id_ in DEFAULT_TIEBREAK_ORDER])
        self.cap_spin.Enable(self.cap_chk.GetValue())

        self.setup_infobar = self._build_infobar()

        self.presenter = SetupPresenter(self, roster, config)

        self._bind_events()

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this dialog's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this dialog, even
                after settling.
        """
        return find_control(self.dialog, name, expected_type)

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build the code-side :data:`SETUP_INFOBAR`, wrapped on top.

        See ``rider_editor.RiderEditor._build_infobar``'s docstring
        for the measured slide-effect hang this mirrors -- the reason
        it disables both show/hide effects too. ``setup.xrc``'s own
        top sizer has no reserved InfoBar slot either (it predates
        this decision, the same as ``rider_editor_dlg``/``csv_
        preview_dlg``), so the bar wraps the existing sizer instead
        of inserting into it.
        """
        bar = wx.InfoBar(self.dialog)
        bar.SetName(SETUP_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        content = self.dialog.GetSizer()
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(bar, 0, wx.EXPAND)
        outer.Add(content, 1, wx.EXPAND)
        self.dialog.SetSizer(outer, deleteOld=False)
        return bar

    def _bind_events(self) -> None:
        """Forward every control event straight to the presenter."""
        self.dialog.Bind(wx.EVT_RADIOBUTTON, self._on_entry_mode_radio, self.solo_radio)
        self.dialog.Bind(wx.EVT_RADIOBUTTON, self._on_entry_mode_radio, self.mixed_radio)
        self.dialog.Bind(wx.EVT_CHECKBOX, self._on_cap_toggle, self.cap_chk)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_ok, self.ok_btn)

    def _on_entry_mode_radio(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle a solo_radio/mixed_radio click; forward it on."""
        event.Skip()
        mode = EntryMode.MIXED if self.mixed_radio.GetValue() else EntryMode.SOLO
        self.presenter.on_entry_mode_changed(mode)

    def _on_cap_toggle(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle cap_chk: gate cap_spin's own enabled state (R-20).

        Purely mechanical (a control's own enabled state tracking a
        sibling checkbox), so this stays in the view rather than
        round-tripping the presenter -- ``RiderEditor``'s own
        ``set_team_ui_visible`` is the one existing precedent for a
        view computing a sibling-control visibility/enablement fact
        structurally rather than through the presenter.
        """
        event.Skip()
        self.cap_spin.Enable(self.cap_chk.GetValue())

    def _on_ok(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx ships no stubs
        """Handle ``wxID_OK``: submit, then close if it committed.

        Measured: ``wxID_OK`` is a stock id wx auto-binds to
        ``EndModal(wx.ID_OK)`` on any ``EVT_BUTTON`` whose handler
        calls ``event.Skip()`` (``CsvPreviewDialog._on_import``'s own
        docstring) -- *event* is never skipped here for the identical
        reason: this handler alone decides whether the dialog closes.
        A refused submit leaves the dialog open, showing why on
        :data:`SETUP_INFOBAR`.
        """
        config = self.presenter.on_submit(self._form_values())
        if config is not None:
            self.config = config
            if self.on_submitted is not None:
                self.on_submitted(config)
            self.dialog.EndModal(wx.ID_OK)

    def _form_values(self) -> SetupFormValues:
        """Return this dialog's current fields, read verbatim (R-20).

        ``entry_mode``/``plate_model``/``jokers_per_deck`` and the W4
        radio pair are the exception each: wx has no "enum radio
        group" control, so translating which radio is checked into a
        domain value is this method's own mechanical job (module
        docstring, mirroring ``RiderEditor._form_values``'s own note
        about ``team_choice``) -- ``hold_short_laps`` reads the
        pair's first member, whose checked state means the operator
        opted into R-34's hold path.
        """
        picked_date = self.date_picker.GetValue()
        picked_time = self.start_time_picker.GetValue()
        logo_text = self.logo_picker.GetPath()
        event_date = date(picked_date.GetYear(), picked_date.GetMonth() + 1, picked_date.GetDay())
        start_time = time(picked_time.GetHour(), picked_time.GetMinute(), picked_time.GetSecond())
        return SetupFormValues(
            name=self.name_input.GetValue(),
            event_date=event_date,
            venue=self.venue_input.GetValue(),
            lap_km=self.lap_km_spin.GetValue(),
            organizer=self.organizer_input.GetValue(),
            scorer=self.scorer_input.GetValue(),
            start_time=start_time,
            duration_text=self.duration_input.GetValue(),
            min_lap_text=self.min_lap_input.GetValue(),
            hold_short_laps=self.hold_short_radio.GetValue(),
            entry_mode=EntryMode.MIXED if self.mixed_radio.GetValue() else EntryMode.SOLO,
            max_team_size=self.team_size_spin.GetValue(),
            plate_model=(
                PlateModel.TEAM_RELAY if self.relay_radio.GetValue() else PlateModel.RIDER_POOLED
            ),
            deck_count=self.decks_spin.GetValue(),
            jokers_per_deck=self._jokers_per_deck(),
            cap_enabled=self.cap_chk.GetValue(),
            max_cards=self.cap_spin.GetValue(),
            tiebreak_order=self._tiebreak_order(),
            logo_path=Path(logo_text) if logo_text else None,
        )

    def _jokers_per_deck(self) -> int:
        """Return 0/2/4 for whichever jokers_*_radio is checked."""
        if self.jokers_0_radio.GetValue():
            return 0
        if self.jokers_4_radio.GetValue():
            return 4
        return 2

    def _tiebreak_order(self) -> tuple[str, str, str]:
        """Return tiebreak_list's current row order as tiebreak ids.

        Falls back to :data:`~rivercrossing.ride.
        DEFAULT_TIEBREAK_ORDER` on anything other than exactly the
        three known rows (module docstring's own New/Delete gap
        note), rather than raise on an unrecognised label.
        """
        labels = tuple(self.tiebreak_list.GetStrings())
        if len(labels) != len(DEFAULT_TIEBREAK_ORDER):
            return DEFAULT_TIEBREAK_ORDER
        try:
            return cast(
                "tuple[str, str, str]", tuple(_TIEBREAK_IDS_BY_LABEL[label] for label in labels)
            )
        except KeyError:
            return DEFAULT_TIEBREAK_ORDER

    def set_team_fields_enabled(self, *, enabled: bool) -> None:
        """Enable relay_radio/team_size_spin (``SetupView``, R-11).

        The presenter's own *enabled* already folds "not locked" in
        (``SetupPresenter._load``'s docstring) -- this method never
        also consults lock state itself, and :meth:`set_entry_locked`
        never touches these same two controls, so exactly one call
        ever decides their enabled state (the measured overlap bug
        this split fixes).
        """
        self.team_size_spin.Enable(enabled)
        self.relay_radio.Enable(enabled)

    def set_entry_locked(self, *, locked: bool) -> None:
        """Lock solo_radio/mixed_radio/pooled_radio (``SetupView``).

        Deliberately excludes relay_radio/team_size_spin -- see this
        method's own protocol docstring (``presenters.setup.
        SetupView``) for why.
        """
        for control in (self.solo_radio, self.mixed_radio, self.pooled_radio):
            control.Enable(not locked)

    def show_deck_count(self, count: int) -> None:
        """Render decks_spin (``SetupView``); XRC leaves it unset."""
        self.decks_spin.SetValue(count)

    def show_lap_km(self, lap_km: float) -> None:
        """Render lap_km_spin (``SetupView``); XRC leaves it unset.

        W4's mirror of :meth:`show_deck_count`: the presenter pushes
        :data:`~rivercrossing.ride.DEFAULT_LAP_KM` so a fresh dialog
        never submits a 0.0 lap length.
        """
        self.lap_km_spin.SetValue(lap_km)

    def show_entry_settings(
        self, *, entry_mode: EntryMode, max_team_size: int, plate_model: PlateModel
    ) -> None:
        """Render the roster's entry/team-size/plate trio."""
        if entry_mode is EntryMode.MIXED:
            self.mixed_radio.SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
        else:
            self.solo_radio.SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
        self.team_size_spin.SetValue(max_team_size)
        if plate_model is PlateModel.TEAM_RELAY:
            self.relay_radio.SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool
        else:
            self.pooled_radio.SetValue(True)  # noqa: FBT003 -- wx API takes a positional bool

    def show_name(self, name: str) -> None:
        """Render name_input from the ride record (``SetupView``)."""
        self.name_input.SetValue(name)

    def show_date(self, event_date: date) -> None:
        """Render date_picker from the ride record (``SetupView``)."""
        self.date_picker.SetValue(
            wx.DateTime(event_date.day, event_date.month - 1, event_date.year)
        )

    def show_start_time(self, start_time: time) -> None:
        """Render start_time_picker from the ride record (D2)."""
        self.start_time_picker.SetTime(start_time.hour, start_time.minute, start_time.second)

    def show_venue(self, venue: str) -> None:
        """Render venue_input from the ride record (``SetupView``)."""
        self.venue_input.SetValue(venue)

    def show_organizer(self, organizer: str) -> None:
        """Render organizer_input from the ride record (D2)."""
        self.organizer_input.SetValue(organizer)

    def show_scorer(self, scorer: str) -> None:
        """Render scorer_input from the ride record (``SetupView``)."""
        self.scorer_input.SetValue(scorer)

    def show_duration(self, seconds: int) -> None:
        """Render duration_input's "H:MM" text from the record (D2)."""
        self.duration_input.SetValue(_format_duration(seconds))

    def show_min_lap(self, seconds: int) -> None:
        """Render min_lap_input's "M:SS" text from the record (D2)."""
        self.min_lap_input.SetValue(_format_min_lap(seconds))

    def show_short_lap_policy(self, *, hold_short_laps: bool) -> None:
        """Check the W4 radio pair's stored policy (D2)."""
        self.hold_short_radio.SetValue(hold_short_laps)
        self.always_deal_radio.SetValue(not hold_short_laps)

    def show_jokers_per_deck(self, count: int) -> None:
        """Check jokers_0/2/4_radio from the record (D2)."""
        self.jokers_2_radio.SetValue(count == DEFAULT_JOKERS_PER_DECK)
        self.jokers_0_radio.SetValue(count == 0)
        self.jokers_4_radio.SetValue(count == JOKERS_4_PER_DECK)

    def show_card_cap(self, max_cards: int | None) -> None:
        """Render cap_chk/cap_spin; ``None`` means uncapped (D2)."""
        self.cap_chk.SetValue(max_cards is not None)
        self.cap_spin.SetValue(max_cards if max_cards is not None else 1)
        self.cap_spin.Enable(self.cap_chk.GetValue())

    def show_tiebreak_order(self, order: tuple[str, str, str]) -> None:
        """Render tiebreak_list's rows from the record (D2).

        Rows are the same plain labels the New Ride seed uses (this
        module's own docstring); an unrecognised stored id falls back
        to the whole default order, exactly as :meth:`_tiebreak_order`
        does in the other direction.
        """
        labels = [_TIEBREAK_LABELS.get(id_, "") for id_ in order]
        if "" in labels:
            labels = [_TIEBREAK_LABELS[id_] for id_ in DEFAULT_TIEBREAK_ORDER]
        self.tiebreak_list.SetStrings(labels)

    def show_logo(self, logo_path: Path | None) -> None:
        """Render logo_picker from the ride record (``SetupView``)."""
        self.logo_picker.SetPath(str(logo_path) if logo_path is not None else "")

    def set_structure_enabled(self, *, enabled: bool) -> None:
        """Gate the ride-shape controls to a DRAFT ride (D2).

        Entry mode, plate model, decks, jokers, the card cap and the
        tie-break order: a started ride's format is fixed, so these go
        read-only while a live ride keeps its shoe and roster. The
        name/date/start/venue/organizer/scorer fields are deliberately
        untouched -- D2 keeps them editable in every state.
        """
        for control in (
            self.solo_radio,
            self.mixed_radio,
            self.pooled_radio,
            self.relay_radio,
            self.decks_spin,
            self.jokers_0_radio,
            self.jokers_2_radio,
            self.jokers_4_radio,
            self.cap_chk,
            self.tiebreak_list,
        ):
            control.Enable(enabled)
        # cap_spin already tracks cap_chk (the view's own toggle); the
        # structure gate only ever narrows that, never widens it.
        self.cap_spin.Enable(enabled and self.cap_chk.GetValue())

    def show_validation(self, message: str) -> None:
        """Show *message* on :data:`SETUP_INFOBAR` (``SetupView``)."""
        self.setup_infobar.ShowMessage(message, wx.ICON_WARNING)
        self.dialog.Layout()
