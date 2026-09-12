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
is what :meth:`SetupPresenter._load` pushes to it. W4 adds the
mirror seam for ``lap_km_spin``: XRC declares no value there either,
so a fresh ``wxSpinCtrlDouble`` sits at 0.0 and the minimum-setup
gate would refuse every untouched submit -- :meth:`SetupPresenter.
_load` now pushes :data:`~rivercrossing.ride.DEFAULT_LAP_KM` (8.0,
the canvas's own drawing) through the new
:meth:`SetupView.show_lap_km` view seam. ``entry_mode``/
``max_team_size``/``plate_model`` are the mirror image -- XRC *does*
declare defaults for their controls (mixed/pooled/4 -- the entry-mode
default now sits on ``mixed_radio``, teams-first, per setup.xrc's own
header comment), but opening setup on a live roster must show that
roster's own values instead
(xrc-windows.md's own "field values are loaded from the ride record"
footnote); :meth:`SetupPresenter._load` overrides XRC there too. The
W4 short-lap policy radio pair (``hold_short_radio``/
``always_deal_radio``, always-deal checked by XRC) translates to the
boolean ``SetupFormValues.hold_short_laps`` the view reads straight
off the radio, and ``on_submit`` carries it onto
:class:`~rivercrossing.ride.RideConfig`.

:meth:`SetupPresenter.on_submit` refuses a form whose built config
fails the minimum-setup rule -- blank name/venue/organizer/scorer or
a non-positive lap length, :func:`rivercrossing.ride.
setup_minimum_violations`, the same floor a DRAFT ride's start
enforces -- by joining every reason into one
:meth:`SetupView.show_validation` message and returning ``None``;
the config is still built first so :class:`~rivercrossing.ride.
RideConfig`'s own bound errors keep their refusal path unchanged.

D2 gives that same dialog a second mode. Passed a
:class:`~rivercrossing.ride.RideConfig` it opens on that ride's own
values instead of the defaults (Ride ▸ Edit Ride…), which is what the
view's ``show_*`` preload seams below are for; every field the ride
record carries is pushed once, at load. The ride-*shape* controls
(entry mode, plate model, decks, jokers, card cap and tie-break order)
are gated on the ride's own state -- editable while it is DRAFT, read
only after that, since a started ride's format is fixed (R-15/R-17's
own DRAFT-only rule, extended here to the setup dialog). Name, date,
planned start, venue, organizer and scorer stay editable in every
state: those are facts about the event, not about its structure.

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

from rivercrossing.ride import (
    DEFAULT_DECK_COUNT,
    DEFAULT_LAP_KM,
    RideConfig,
    RideConfigError,
    setup_minimum_violations,
)
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
    view) -- except ``entry_mode``/``plate_model``/``jokers_per_deck``/
    ``hold_short_laps``, which the view *must* translate (which radio
    is checked -> which enum/int/bool value), since wx has no "enum
    radio group" control of its own; the same kind of view-side
    translation :class:`RiderEditor`'s own ``team_choice`` reading
    already does. ``duration_text``/``min_lap_text`` stay raw
    "H:MM"/"M:SS" strings -- ``duration_input``/``min_lap_input`` are
    plain ``wxTextCtrl``, so parsing them into seconds is this
    module's own job (:func:`_parse_duration`/:func:`_parse_min_lap`),
    not the view's. ``hold_short_laps`` mirrors the W4 radio pair:
    True when ``hold_short_radio`` is checked, False (always deal)
    when ``always_deal_radio`` is -- the pair's XRC default.
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
    hold_short_laps: bool
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
        """Render decks_spin -- one of the two XRC-unset controls."""
        ...

    def show_lap_km(self, lap_km: float) -> None:
        """Render lap_km_spin -- the other XRC-unset control (W4).

        A bare ``wxSpinCtrlDouble`` sits at 0.0, which the minimum-
        setup gate would refuse, so :meth:`SetupPresenter._load`
        pushes :data:`~rivercrossing.ride.DEFAULT_LAP_KM` exactly as
        it pushes the deck count.
        """
        ...

    def show_entry_settings(
        self, *, entry_mode: EntryMode, max_team_size: int, plate_model: PlateModel
    ) -> None:
        """Render the roster's entry_mode/max_team_size/plate_model."""
        ...

    def show_name(self, name: str) -> None:
        """Render name_input from the ride record (D2 preload)."""
        ...

    def show_date(self, event_date: date) -> None:
        """Render date_picker from the ride record (D2 preload)."""
        ...

    def show_start_time(self, start_time: time) -> None:
        """Render start_time_picker from the ride record (D2)."""
        ...

    def show_venue(self, venue: str) -> None:
        """Render venue_input from the ride record (D2 preload)."""
        ...

    def show_organizer(self, organizer: str) -> None:
        """Render organizer_input from the ride record (D2 preload)."""
        ...

    def show_scorer(self, scorer: str) -> None:
        """Render scorer_input from the ride record (D2 preload)."""
        ...

    def show_duration(self, seconds: int) -> None:
        """Render duration_input's "H:MM" text from the ride record."""
        ...

    def show_min_lap(self, seconds: int) -> None:
        """Render min_lap_input's "M:SS" text from the ride record."""
        ...

    def show_short_lap_policy(self, *, hold_short_laps: bool) -> None:
        """Check hold_short_radio or always_deal_radio (D2 preload)."""
        ...

    def show_jokers_per_deck(self, count: int) -> None:
        """Check jokers_0/2/4_radio from the record (D2)."""
        ...

    def show_card_cap(self, max_cards: int | None) -> None:
        """Render cap_chk/cap_spin (``None`` = uncapped, D2 preload)."""
        ...

    def show_tiebreak_order(self, order: tuple[str, str, str]) -> None:
        """Render tiebreak_list's rows from the record (D2)."""
        ...

    def show_logo(self, logo_path: Path | None) -> None:
        """Render the logo column from the ride record (D2 preload).

        ``None`` blanks ``logo_preview_bmp`` back to the "NO LOGO"
        default; a stored path renders its PNG into the preview box
        (plan section 3d -- the standalone ``logo_picker`` row and its
        ``GetPath()`` seam are retired).
        """
        ...

    def set_structure_enabled(self, *, enabled: bool) -> None:
        """Enable the D2 structural group (DRAFT-only edits).

        The entry-mode radios, the plate-model radios, decks_spin,
        the jokers radios, cap_chk/cap_spin and tiebreak_list: the
        ride-shape fields a started ride may no longer change. The
        name/date/start/venue/organizer/scorer fields stay editable in
        every state, and :meth:`set_entry_locked`'s relay lock is
        unchanged -- this is the *other* half of the D2 gate.
        """
        ...

    def show_validation(self, message: str) -> None:
        """Show a refused-submit message on the view's setup infobar."""
        ...


def _parse_duration(text: str) -> int:
    """Parse ``duration_input``'s "H:MM" into whole seconds (spec §2).

    A stripped-empty *text* gets its own refusal message (W4: a
    fresh dialog submits blank until the operator fills the field --
    "blank" names the gap, "format" names a wrong shape).

    Raises:
        ValueError: *text* is blank, or not exactly one ``H:MM``
            pair of integers.
    """
    if not text.strip():
        msg = "Duration is empty and must be completed"
        raise ValueError(msg)
    try:
        hours_text, minutes_text = text.split(":")
        return int(hours_text) * 3600 + int(minutes_text) * 60
    except ValueError as exc:
        msg = f"Duration must be H:MM, got {text!r}"
        raise ValueError(msg) from exc


def _format_duration(seconds: int) -> str:
    """Render whole *seconds* as ``duration_input``'s "H:MM" (D2).

    :func:`_parse_duration`'s inverse for a stored ride's
    ``planned_duration_s``: the field has no seconds column, so any
    remainder is dropped exactly as the parser's own "H:MM" shape
    implies (21600 -> "6:00").
    """
    hours, remainder = divmod(seconds, 3600)
    return f"{hours}:{remainder // 60:02d}"


def _format_min_lap(seconds: int) -> str:
    """Render whole *seconds* as ``min_lap_input``'s "M:SS" (D2).

    :func:`_parse_min_lap`'s inverse for a stored ride's ``min_lap_s``
    (1080 -> "18:00"). Minutes are never folded into hours: the field
    is a lap's own duration, not a clock time (spec §6).
    """
    minutes, remainder = divmod(seconds, 60)
    return f"{minutes}:{remainder:02d}"


def _parse_min_lap(text: str) -> int:
    """Parse ``min_lap_input``'s "M:SS" into whole seconds (spec §6).

    A stripped-empty *text* gets its own refusal message (W4), the
    mirror of :func:`_parse_duration`'s blank guard.

    Raises:
        ValueError: *text* is blank, or not exactly one ``M:SS``
            pair of integers.
    """
    if not text.strip():
        msg = "Min lap is blank and must be completed"
        raise ValueError(msg)
    try:
        minutes_text, seconds_text = text.split(":")
        return int(minutes_text) * 60 + int(seconds_text)
    except ValueError as exc:
        msg = f"Min lap must be M:SS, got {text!r}"
        raise ValueError(msg) from exc


class SetupPresenter:
    """Presenter for the ride setup dialog (ride_setup_dlg, R-17)."""

    def __init__(self, view: SetupView, roster: Roster, config: RideConfig | None = None) -> None:
        """Store the view, roster and ride config this presenter drives.

        Args:
            view: The setup view driving this roster.
            roster: The in-memory roster whose current entry_mode/
                max_team_size/plate_model/status this dialog reads
                (module docstring) -- never written back to directly.
            config: The ride being edited (D2's Edit Ride…), or
                ``None`` for a New Ride. When given, every field is
                preloaded from it and the structural group is gated on
                the ride's own state; when ``None`` the dialog opens on
                the E3.5 defaults.
        """
        self.view = view
        self.roster = roster
        self.config = config
        self._load()

    def _load(self) -> None:
        """Render ride_setup_dlg's initial state (module docstring).

        Two modes, one dialog: a New Ride loads the E3.5 defaults
        (decks/lap_km from the presenter, entry settings from the
        roster), an Edit Ride (D2) preloads every field from the ride
        record instead -- xrc-windows.md's own "field values are loaded
        from the ride record" footnote, now with a ride record to read.

        ``set_team_fields_enabled``'s own argument folds in *locked*
        too (measured bug, fixed here): ``relay_radio``/
        ``team_size_spin`` are ``SetupView.set_team_fields_enabled``'s
        exclusive scope, never ``set_entry_locked``'s -- calling both
        independently, in either order, let whichever ran last
        silently undo the other's effect on those two controls.
        ``set_structure_enabled`` is the independent D2 gate over the
        ride-*shape* controls (entry mode, plate model, cards).
        """
        config = self.config
        if config is None:
            self._load_defaults()
            entry_mode, plate_model = self.roster.entry_mode, self.roster.plate_model
        else:
            self._load_from_config(config)
            entry_mode, plate_model = config.entry_mode, config.plate_model
        locked = self._entry_locked(plate_model)
        self.view.set_team_fields_enabled(enabled=entry_mode is EntryMode.MIXED and not locked)
        self.view.set_entry_locked(locked=locked)
        self.view.set_structure_enabled(enabled=can_edit_structure(self.roster.status))

    def _load_defaults(self) -> None:
        """Render a New Ride's own defaults (E3.5, W4)."""
        self.view.show_deck_count(DEFAULT_DECK_COUNT)
        self.view.show_lap_km(DEFAULT_LAP_KM)
        self.view.show_entry_settings(
            entry_mode=self.roster.entry_mode,
            max_team_size=self.roster.max_team_size,
            plate_model=self.roster.plate_model,
        )

    def _load_from_config(self, config: RideConfig) -> None:
        """Render every field of the ride being edited (D2 preload)."""
        self.view.show_name(config.name)
        self.view.show_date(config.event_date)
        self.view.show_start_time(config.planned_start.time())
        self.view.show_venue(config.venue)
        self.view.show_organizer(config.organizer)
        self.view.show_scorer(config.scorer)
        self.view.show_lap_km(config.lap_km)
        self.view.show_duration(config.planned_duration_s)
        self.view.show_min_lap(config.min_lap_s)
        self.view.show_short_lap_policy(hold_short_laps=config.hold_short_laps)
        self.view.show_entry_settings(
            entry_mode=config.entry_mode,
            max_team_size=config.max_team_size,
            plate_model=config.plate_model,
        )
        self.view.show_deck_count(config.deck_count)
        self.view.show_jokers_per_deck(config.jokers_per_deck)
        self.view.show_card_cap(config.max_cards)
        self.view.show_tiebreak_order(config.tiebreak_order)
        self.view.show_logo(config.logo_path)

    def _entry_locked(self, plate_model: PlateModel) -> bool:
        """Return whether the entry/plate-model group should lock.

        R-17: locks once the ride has left DRAFT, for a relay ride
        only -- a pooled ride's plate model stays editable in every
        state (xrc-windows.md's own ride_setup_dlg footnote). The
        plate model comes from the config when one is edited, so the
        lock never disagrees with the value the dialog submits back.
        """
        return not can_edit_structure(self.roster.status) and plate_model is PlateModel.TEAM_RELAY

    def on_entry_mode_changed(self, entry_mode: EntryMode) -> None:
        """Handle a live solo_radio/mixed_radio selection change."""
        self.view.set_team_fields_enabled(enabled=entry_mode is EntryMode.MIXED)

    def on_submit(self, form: SetupFormValues) -> RideConfig | None:
        """Build a validated RideConfig from *form* (wxID_OK, R-20).

        A refusal (an unparsable duration/min-lap, a RideConfig-level
        bound violation such as an out-of-range max_team_size, or a
        minimum-setup violation -- blank name/venue/organizer/scorer
        or a non-positive lap length, the start gate's own rule via
        :func:`~rivercrossing.ride.setup_minimum_violations`, whose
        reasons are joined into one message) shows via
        :meth:`SetupView.show_validation` and returns ``None``,
        never raising past this handler -- the same refusal shape
        :class:`~rivercrossing.ui.presenters.riders.RidersPresenter`'s
        own handlers use. The config is built first so the minimum-
        setup check runs on the real object (module docstring).

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
                hold_short_laps=form.hold_short_laps,
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
        violations = setup_minimum_violations(config)
        if violations:
            self.view.show_validation("; ".join(violations))
            return None
        return config
