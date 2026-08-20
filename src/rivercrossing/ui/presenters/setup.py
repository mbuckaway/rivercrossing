# SPDX-License-Identifier: GPL-3.0-only
"""Setup presenter -- ride_setup_dlg (7a), ride configuration (E3.5).

``SetupPresenter`` drives ``ride_setup_dlg`` from a real, in-memory
:class:`~rivercrossing.roster.Roster` (E3.5.1) -- the same E3.2.1
shift ``RidersPresenter`` already made: it takes ``(view, roster)``,
not ``(view, data_source)``, since the dialog reads a ride's own live
entry/plate-model settings from the roster and writes nothing back to
it directly (``on_submit`` builds a stand-alone
:class:`~rivercrossing.ride.RideConfig` instead -- E4's ``RideEngine``
is the eventual consumer, module-skeletons.md:158).

``decks_spin`` is ``ride_setup_dlg``'s one XRC-unset control
(setup.xrc's own header comment): :data:`~rivercrossing.ride.
DEFAULT_DECK_COUNT` (8, spec §4's own binding decision, 2026-08-08)
is what :meth:`SetupPresenter._load` pushes to it. ``entry_mode``/
``max_team_size``/``plate_model`` are the mirror image -- XRC *does*
declare defaults for their controls (solo/pooled/4), but opening
setup on a live roster must show that roster's own values instead
(xrc-windows.md's own "field values are loaded from the ride record"
footnote); :meth:`SetupPresenter._load` overrides XRC there too.

The entry/plate-model lock (R-17) is a static fact about the roster
setup opened on -- a ride's status never changes while this dialog is
open -- so :meth:`SetupPresenter._load` computes and pushes it once;
:meth:`on_entry_mode_changed` is the one thing that *does* need to
react live, since the operator is actively editing entry_mode in this
same dialog.

Pure Python -- no ``wx`` import may ever land here (R-71).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing.ride import DEFAULT_DECK_COUNT, RideConfig, RideConfigError
from rivercrossing.roster import EntryMode, PlateModel, can_edit_structure

if TYPE_CHECKING:
    from datetime import date, time
    from pathlib import Path

    from rivercrossing.roster import Roster

__all__ = ["SetupFormValues", "SetupPresenter", "SetupView"]


@dataclass(frozen=True, slots=True, kw_only=True)
class SetupFormValues:
    """``ride_setup_dlg``'s raw submitted form, forwarded verbatim (7a).

    Mirrors :class:`~rivercrossing.ui.presenters.riders.
    RiderFormValues`'s own precedent: every field is read exactly as
    its control holds it, never translated by the view (passive
    view) -- except ``entry_mode``/``plate_model``/``jokers_per_deck``,
    which the view *must* translate (which radio is checked -> which
    enum/int value), since wx has no "enum radio group" control of its
    own; the same kind of view-side translation :class:`RiderEditor`'s
    own ``team_choice`` reading already does. ``duration_text``/
    ``min_lap_text`` stay raw "H:MM"/"M:SS" strings -- ``duration_
    input``/``min_lap_input`` are plain ``wxTextCtrl``, so parsing
    them into seconds is this module's own job
    (:func:`_parse_duration`/:func:`_parse_min_lap`), not the view's.
    """

    name: str
    event_date: date
    venue: str
    lap_km: float
    organizer: str
    scorer: str
    start_time: time
    duration_text: str
    min_lap_text: str
    entry_mode: EntryMode
    max_team_size: int
    plate_model: PlateModel
    deck_count: int
    jokers_per_deck: int
    cap_enabled: bool
    max_cards: int
    tiebreak_order: tuple[str, str, str]
    logo_path: Path | None


@runtime_checkable
class SetupView(Protocol):
    """View surface for the ride setup dialog (ride_setup_dlg, 7a)."""

    def set_team_fields_enabled(self, *, enabled: bool) -> None:
        """Enable relay_radio/team_size_spin (mixed_radio AND unlocked).

        The one place these two controls' own enabled state is ever
        set -- ``set_entry_locked`` never touches them (its own
        docstring).
        """
        ...

    def set_entry_locked(self, *, locked: bool) -> None:
        """Lock solo_radio/mixed_radio/pooled_radio (relay post-start).

        Never touches relay_radio/team_size_spin: those are
        ``set_team_fields_enabled``'s own exclusive scope, which
        already folds *locked* into its own argument.
        """
        ...

    def show_deck_count(self, count: int) -> None:
        """Render decks_spin -- the one control XRC leaves unset."""
        ...

    def show_entry_settings(
        self, *, entry_mode: EntryMode, max_team_size: int, plate_model: PlateModel
    ) -> None:
        """Render the roster's entry_mode/max_team_size/plate_model."""
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-submit message (setup_infobar, later)."""
        ...


def _parse_duration(text: str) -> int:
    """Parse ``duration_input``'s "H:MM" into whole seconds (spec §2).

    Raises:
        ValueError: *text* is not exactly one ``H:MM`` pair of
            integers.
    """
    try:
        hours_text, minutes_text = text.split(":")
        return int(hours_text) * 3600 + int(minutes_text) * 60
    except ValueError as exc:
        msg = f"Duration must be H:MM, got {text!r}"
        raise ValueError(msg) from exc


def _parse_min_lap(text: str) -> int:
    """Parse ``min_lap_input``'s "M:SS" into whole seconds (spec §6).

    Raises:
        ValueError: *text* is not exactly one ``M:SS`` pair of
            integers.
    """
    try:
        minutes_text, seconds_text = text.split(":")
        return int(minutes_text) * 60 + int(seconds_text)
    except ValueError as exc:
        msg = f"Min lap must be M:SS, got {text!r}"
        raise ValueError(msg) from exc


class SetupPresenter:
    """Presenter for the ride setup dialog (ride_setup_dlg, R-17)."""

    def __init__(self, view: SetupView, roster: Roster) -> None:
        """Store the view and roster this presenter drives, and load.

        Args:
            view: The setup view driving this roster.
            roster: The in-memory roster whose current entry_mode/
                max_team_size/plate_model/status this dialog reads
                (module docstring) -- never written back to directly.
        """
        self.view = view
        self.roster = roster
        self._load()

    def _load(self) -> None:
        """Render ride_setup_dlg's initial state (module docstring).

        ``set_team_fields_enabled``'s own argument folds in *locked*
        too (measured bug, fixed here): ``relay_radio``/
        ``team_size_spin`` are ``SetupView.set_team_fields_enabled``'s
        exclusive scope, never ``set_entry_locked``'s -- calling both
        independently, in either order, let whichever ran last
        silently undo the other's effect on those two controls.
        """
        self.view.show_deck_count(DEFAULT_DECK_COUNT)
        self.view.show_entry_settings(
            entry_mode=self.roster.entry_mode,
            max_team_size=self.roster.max_team_size,
            plate_model=self.roster.plate_model,
        )
        locked = self._entry_locked()
        self.view.set_team_fields_enabled(
            enabled=self.roster.entry_mode is EntryMode.MIXED and not locked
        )
        self.view.set_entry_locked(locked=locked)

    def _entry_locked(self) -> bool:
        """Return whether the entry/plate-model group should lock.

        R-17: locks once the ride has left DRAFT, for a relay ride
        only -- a pooled ride's plate model stays editable in every
        state (xrc-windows.md's own ride_setup_dlg footnote).
        """
        return not can_edit_structure(self.roster.status) and self.roster.plate_model is (
            PlateModel.TEAM_RELAY
        )

    def on_entry_mode_changed(self, entry_mode: EntryMode) -> None:
        """Handle a live solo_radio/mixed_radio selection change."""
        self.view.set_team_fields_enabled(enabled=entry_mode is EntryMode.MIXED)

    def on_submit(self, form: SetupFormValues) -> RideConfig | None:
        """Build a validated RideConfig from *form* (wxID_OK, R-20).

        A refusal (an unparsable duration/min-lap, or a RideConfig-
        level bound violation such as an out-of-range max_team_size)
        shows via :meth:`SetupView.show_validation` and returns
        ``None``, never raising past this handler -- the same
        refusal shape :class:`~rivercrossing.ui.presenters.riders.
        RidersPresenter`'s own handlers use.

        Returns:
            The built :class:`RideConfig`, or ``None`` on a refused
            submit.
        """
        try:
            planned_start = datetime.combine(form.event_date, form.start_time)
            duration_s = _parse_duration(form.duration_text)
            min_lap_s = _parse_min_lap(form.min_lap_text)
            config = RideConfig(
                name=form.name,
                event_date=form.event_date,
                venue=form.venue,
                lap_km=form.lap_km,
                organizer=form.organizer,
                scorer=form.scorer,
                planned_start=planned_start,
                planned_duration_s=duration_s,
                min_lap_s=min_lap_s,
                entry_mode=form.entry_mode,
                max_team_size=form.max_team_size,
                plate_model=form.plate_model,
                deck_count=form.deck_count,
                jokers_per_deck=form.jokers_per_deck,
                max_cards=form.max_cards if form.cap_enabled else None,
                tiebreak_order=form.tiebreak_order,
                logo_path=form.logo_path,
            )
        except (RideConfigError, ValueError) as exc:
            self.view.show_validation(str(exc))
            return None
        return config
