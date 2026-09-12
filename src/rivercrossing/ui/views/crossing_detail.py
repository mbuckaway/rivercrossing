# SPDX-License-Identifier: GPL-3.0-only
"""Crossing Detail: the console feed's row dialog (J2), two modes (K2).

Double-clicking a row in the console's ``crossings_list`` opens
``crossing_detail_dlg`` (``dialogs.xrc``) on that row. A **crossing**
row shows every fact the feed compresses into seven columns -- the
rider the typed plate belongs to, the entry's team, the plate, the lap
number, the crossing/lap/total times, the dealt card's glyph and that
card's disposition -- plus the two corrections the console already
owns: OK commits an edited plate through
:meth:`~rivercrossing.ride.RideEngine.reassign_crossing` (the console's
own "wrong plate on the line" correction), and Delete -- offered only
for the newest crossing -- runs
:meth:`~rivercrossing.ride.RideEngine.undo_last`, exactly the console's
Undo button, after a danger confirm.

A **miss** row (a pending miss, K) opens the same dialog in miss mode:
:class:`MissDetailView` renders the placeholders the feed row itself
shows (Plate ``-``, Name ``missed``) and its one action is to score the
miss -- OK assigns the typed plate through
:meth:`~rivercrossing.ride.RideEngine.assign_plate_to_miss`, which
records the crossing and deals its card at the miss's own instant.
Delete is not offered: a miss is not ``engine.crossings[-1]`` and cannot
be undone.

A refusal surfaces on a code-side ``wxInfoBar``
(:data:`CROSSING_DETAIL_INFOBAR`) and leaves the dialog open to fix:
``dialogs.xrc`` cannot author a ``wxInfoBar`` at all, so it is wrapped
around the dialog's existing sizer with both slide effects disabled --
the measured hang remedy ``RiderEditor._build_infobar`` documents.

The rendered values come from the pure view-models :func:`build_fields`
(over the live ``Crossing``, ``Roster`` and ``RideEngine``) and
:func:`build_miss_fields` (over the ``PendingMiss``) -- no ``wx`` -- so
the field mapping is pinned headlessly
(``tests/unit/ui/test_crossing_detail.py``). The views are deliberately
not wired to a presenter: every change they commit goes straight
through the engine, whose ``on_event`` sink persists it
(``app._wire_store_append``), and the console re-renders the feed from
the engine on its own 1 s tick.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import wx

from rivercrossing.ride import RideEngineError
from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.card_text import format_card
from rivercrossing.ui.presenters.data_source import format_duration
from rivercrossing.ui.views import dialogs
from rivercrossing.ui.views._support import find_control

if TYPE_CHECKING:
    from datetime import datetime

    from rivercrossing.ride import Crossing, PendingMiss, RideEngine
    from rivercrossing.roster import Entry, Roster

__all__ = [
    "CROSSING_DETAIL_INFOBAR",
    "EDIT_REASON",
    "MISS_EDIT_REASON",
    "CrossingDetailFields",
    "CrossingDetailView",
    "MissDetailView",
    "build_fields",
    "build_miss_fields",
    "delete_message",
    "is_last_crossing",
]

# ui/ids.py is generated from the .xrc files (R-05);
# crossing_detail_infobar never appears there since XRC cannot author a
# wxInfoBar at all (rider_issues.py's ISSUES_INFOBAR precedent).
CROSSING_DETAIL_INFOBAR = "crossing_detail_infobar"

# The audited reason OK's reassign carries: reassign_crossing refuses a
# blank one and the audit trail shows it verbatim.
EDIT_REASON = "crossing detail edit"

# The audited reason the miss mode's OK carries (K2), the crossing
# EDIT_REASON's own counterpart.
MISS_EDIT_REASON = "miss detail edit"

# The Card-state field's three values. Held is R-34's short-lap
# hold-for-review state; a card neither held nor credited was voided
# out of the ride entirely (RideEngine.void_held).
_HELD_STATUS = "Held — short lap awaiting review"
_CREDITED_STATUS = "Credited"
_VOIDED_STATUS = "Voided"

_LAP_SECONDS_PER_HOUR = 3600

_DELETE_TITLE = "Undo Last Crossing?"

_OK_LABEL = "Undo"
_CANCEL_LABEL = "Cancel"


@dataclass(frozen=True, slots=True)
class CrossingDetailFields:
    """The read-only value each ``crossing_detail_dlg`` label renders.

    One field per frozen ``_lbl`` control, in the dialog's own canvas
    order -- the whole view-model :func:`build_fields` (a crossing) and
    :func:`build_miss_fields` (a pending miss) produce, and
    :meth:`_DetailDialogView._render_fields` writes out.
    """

    rider: str
    team: str
    plate: str
    lap: str
    time: str
    lap_time: str
    total: str
    card: str
    held: str


def _local_time(crossed_at: datetime) -> str:
    """Render a crossing instant as local 24-hour ``HH:MM:SS``.

    spec §13: "Times: stored UTC, displayed local 24-hour." An aware
    UTC datetime (the app's real clock) converts to local; a naive one
    (tests) is displayed as stored -- the console feed's own
    ``_feed_time`` rule, repeated here rather than imported, since
    that helper is private to ``ui.presenters.data_source``.
    """
    local = crossed_at.astimezone() if crossed_at.tzinfo is not None else crossed_at
    return local.strftime("%H:%M:%S")


def _format_lap_time(seconds: float) -> str:
    """Render a per-lap time as ``m:ss`` (``h:mm:ss`` past an hour).

    The console feed's own lap-time format, so the dialog and the row
    it was opened from never disagree. Negative seconds clamp to zero.
    """
    total = max(0, int(seconds))
    if total >= _LAP_SECONDS_PER_HOUR:
        return format_duration(total)
    minutes, secs = divmod(total, 60)
    return f"{minutes}:{secs:02d}"


def _rider_name(entry: Entry | None, rider_plate: str | None) -> str | None:
    """Return the name of *entry*'s rider owning *rider_plate*.

    ``Crossing.rider_plate`` holds the plate the operator typed (J1),
    so a ``rider_pooled`` team's detail names the rider who actually
    crossed. ``None`` covers a missing *entry*, a crossing with no
    recorded rider plate, and a ``team_relay`` ride -- whose riders
    carry no plate (S1) -- so the caller falls back to the entry's own
    display name. Mirrors ``data_source._rider_name_for``, which
    attributes the feed's rows the same way.
    """
    if entry is None or rider_plate is None:
        return None
    for rider in entry.riders:
        if rider.plate == rider_plate:
            return rider.full_name
    return None


def _lap_and_total(engine: RideEngine, crossing: Crossing) -> tuple[float, float]:
    """Return ``(lap time, running total)`` in seconds for *crossing*.

    spec §6: lap times are derived, never stored, so both come from
    ``engine.lap_times`` -- the crossing's own lap, and the sum of
    every lap up to and including it. A crossing whose ``seq`` is past
    the entry's recorded laps (a stale row) renders the zero duration
    instead of indexing off the end.
    """
    times = engine.lap_times(crossing.entry_id)
    if crossing.seq > len(times):
        return 0.0, 0.0
    return times[crossing.seq - 1], sum(times[: crossing.seq])


def _held_status(engine: RideEngine, crossing: Crossing, held: object | None) -> str:
    """Return the card's disposition text for *crossing*.

    Three states, all read off the engine: the card is held for review
    (R-34 -- *held* is :meth:`RideEngine.held_card_for`'s answer),
    it is credited to the entry's hand, or it was voided out of the
    ride entirely. The distinction matters in exactly the dialog a
    scorer opens to check why a card is missing from a hand.
    """
    if held is not None:
        return _HELD_STATUS
    card = engine.card_for(crossing)
    if card in engine.credited_cards(crossing.entry_id):
        return _CREDITED_STATUS
    return _VOIDED_STATUS


def build_fields(crossing: Crossing, roster: Roster, engine: RideEngine) -> CrossingDetailFields:
    """Return the read-only field values for *crossing*.

    The whole view-model, built from the live domain objects: the
    roster resolves which entry the crossing belongs to, the engine
    answers the timing and card questions. Pure -- no ``wx`` -- so the
    mapping is pinned headlessly.
    """
    entry = roster.resolve_plate(crossing.entry_id)
    entry_name = entry.display_name if entry is not None else crossing.entry_id
    rider = _rider_name(entry, crossing.rider_plate)
    lap_time, total = _lap_and_total(engine, crossing)
    # W9's feed rule: a held crossing still shows the real dealt code,
    # never a placeholder -- held_card_for is that answer.
    held = engine.held_card_for(crossing)
    card = held if held is not None else engine.card_for(crossing)
    return CrossingDetailFields(
        rider=rider or entry_name,
        team=entry_name,
        plate=crossing.rider_plate or crossing.entry_id,
        lap=str(crossing.seq),
        time=_local_time(crossing.crossed_at),
        lap_time=_format_lap_time(lap_time),
        total=format_duration(total),
        card=format_card(card.code()),
        held=_held_status(engine, crossing, held),
    )


def build_miss_fields(miss: PendingMiss) -> CrossingDetailFields:
    """Return the read-only field values for a pending *miss* (K2).

    A miss has no entry, lap or card, so only the instant the operator
    signalled it is known; every other cell renders the placeholder the
    feed's own ``-``/``missed`` row uses. The plate a scorer types is
    not one of these labels -- Edit unlocks ``edit_plate_input``, and OK
    commits it through ``assign_plate_to_miss``. Pure -- no ``wx`` --
    so the mapping is pinned headlessly.
    """
    return CrossingDetailFields(
        rider="-",
        team="missed",
        plate="-",
        lap="",
        time=_local_time(miss.crossed_at),
        lap_time="",
        total="",
        card="",
        held="Not yet scored",
    )


def is_last_crossing(engine: RideEngine, crossing: Crossing) -> bool:
    """Return whether *crossing* is the engine's newest crossing.

    Delete's gate: ``undo_last`` removes exactly one crossing -- the
    newest -- so the button is offered only for the crossing
    ``engine.crossings[-1]`` is. Identity, not equality: the app hands
    the view the very object it read out of ``engine.crossings``.
    """
    crossings = engine.crossings
    return bool(crossings) and crossings[-1] is crossing


def delete_message(fields: CrossingDetailFields) -> str:
    """Return Delete's danger-confirm question for *fields*.

    UX-DESKTOP §4: a destructive confirm names the object it is about
    to destroy, so the question carries the crossing's own time, rider
    and lap rather than a bare "Are you sure?".
    """
    return (
        f"Undo crossing {fields.time} · {fields.rider} · lap {fields.lap}? "
        "The newest crossing and its dealt card are removed."
    )


class _DetailDialogView:
    """Widget wiring shared by both modes of ``crossing_detail_dlg``.

    The crossing mode (:class:`CrossingDetailView`) and the miss mode
    (:class:`MissDetailView`) decorate the same frozen dialog, so the
    nine value labels, the plate field, the three buttons, the
    code-side info bar, the Edit handler and Escape all live here.
    Each subclass supplies only what differs: the view-model it renders
    and what OK commits.
    """

    def __init__(self, dialog: wx.Dialog, engine: RideEngine) -> None:
        """Find the frozen controls and build the info bar.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap loaded from
                ``dialogs.xrc``.
            engine: The ride's live engine -- the read side of every
                field and the write side of every correction.
        """
        self.dialog = dialog
        self.engine = engine

        self.crossing_rider_lbl = self._find(ids.CROSSING_RIDER_LBL, wx.StaticText)
        self.crossing_team_lbl = self._find(ids.CROSSING_TEAM_LBL, wx.StaticText)
        self.crossing_plate_lbl = self._find(ids.CROSSING_PLATE_LBL, wx.StaticText)
        self.crossing_lap_lbl = self._find(ids.CROSSING_LAP_LBL, wx.StaticText)
        self.crossing_time_lbl = self._find(ids.CROSSING_TIME_LBL, wx.StaticText)
        self.crossing_lap_time_lbl = self._find(ids.CROSSING_LAP_TIME_LBL, wx.StaticText)
        self.crossing_total_lbl = self._find(ids.CROSSING_TOTAL_LBL, wx.StaticText)
        self.crossing_card_lbl = self._find(ids.CROSSING_CARD_LBL, wx.StaticText)
        self.crossing_held_lbl = self._find(ids.CROSSING_HELD_LBL, wx.StaticText)
        self.edit_plate_input = self._find(ids.EDIT_PLATE_INPUT, wx.TextCtrl)
        self.edit_btn = self._find(ids.EDIT_BTN, wx.Button)
        self.delete_btn = self._find(ids.DELETE_BTN, wx.Button)
        self.ok_btn = self._find(dialogs.WX_ID_OK, wx.Button)

        self.crossing_detail_infobar = self._build_infobar()

        self.dialog.Bind(wx.EVT_BUTTON, self._on_edit, self.edit_btn)
        # Escape is pointed at OK rather than a Cancel button: the
        # window carries no wxID_CANCEL, and wx's own Escape handling
        # only ever looks for one (measured --
        # dialogs.wire_close_button's docstring), so without this the
        # dialog would trap a keyboard-only operator. Escape ends the
        # modal *without* running the subclass's _on_ok, so it
        # discards an uncommitted plate edit -- which is what cancel
        # means (UX-DESKTOP §3: Escape is never the destructive path).
        self.dialog.SetEscapeId(self.ok_btn.GetId())

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
        """Build the code-side :data:`CROSSING_DETAIL_INFOBAR` bar.

        ``dialogs.xrc``'s ``crossing_detail_dlg`` top sizer is a plain
        ``wxBoxSizer`` with no reserved InfoBar slot (XRC cannot author
        a wxInfoBar), so the existing sizer is kept alive and nested
        inside a new outer vertical one instead of edited in the frozen
        XRC. Both slide effects are disabled for the measured hang
        ``RiderEditor._build_infobar`` documents.
        """
        bar = wx.InfoBar(self.dialog)
        bar.SetName(CROSSING_DETAIL_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        content = self.dialog.GetSizer()
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(bar, 0, wx.EXPAND)
        outer.Add(content, 1, wx.EXPAND)
        self.dialog.SetSizer(outer, deleteOld=False)
        return bar

    def _render_fields(self, fields: CrossingDetailFields) -> None:
        """Write *fields* onto the nine read-only labels."""
        self.crossing_rider_lbl.SetLabel(fields.rider)
        self.crossing_team_lbl.SetLabel(fields.team)
        self.crossing_plate_lbl.SetLabel(fields.plate)
        self.crossing_lap_lbl.SetLabel(fields.lap)
        self.crossing_time_lbl.SetLabel(fields.time)
        self.crossing_lap_time_lbl.SetLabel(fields.lap_time)
        self.crossing_total_lbl.SetLabel(fields.total)
        self.crossing_card_lbl.SetLabel(fields.card)
        self.crossing_held_lbl.SetLabel(fields.held)

    def show_refusal(self, message: str) -> None:
        """Show *message* on :data:`CROSSING_DETAIL_INFOBAR`."""
        self.crossing_detail_infobar.ShowMessage(message, wx.ICON_WARNING)
        self.dialog.Layout()

    def _on_edit(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``edit_btn``: unlock the plate field for editing."""
        event.Skip()
        self.edit_plate_input.Enable(True)  # noqa: FBT003 -- wx API takes a positional bool
        self.edit_plate_input.SetFocus()
        self.edit_plate_input.SelectAll()


class CrossingDetailView(_DetailDialogView):
    """Code-side behaviour for ``crossing_detail_dlg`` (J2).

    A read-only detail view with two corrections, not a
    presenter-backed form: it renders :func:`build_fields` once at
    construction and forwards its three buttons straight to the engine
    (``views/rider_issues.py``'s presenter-inside-the-view shape, minus
    the presenter -- there is no view logic here to put in one).
    """

    def __init__(  # noqa: PLR0913 -- (dialog, crossing, roster, engine)
        self,
        dialog: wx.Dialog,
        *,
        crossing: Crossing,
        roster: Roster,
        engine: RideEngine,
    ) -> None:
        """Decorate an already-loaded ``crossing_detail_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap loaded from
                ``dialogs.xrc``.
            crossing: The live crossing this dialog shows.
            roster: The ride's in-memory roster, for the rider and team
                names.
            engine: The ride's live engine -- the read side of every
                field and the write side of both corrections.
        """
        super().__init__(dialog, engine)
        self.crossing = crossing
        self.roster = roster

        # OK alone decides whether the dialog closes, without a Skip
        # (ride_setup._on_ok's measured note): a Skip would let wx's
        # stock OK close the dialog before the commit ran.
        self.dialog.Bind(wx.EVT_BUTTON, self._on_ok, self.ok_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_delete, self.delete_btn)

        self.render()

    def render(self) -> None:
        """Write :func:`build_fields`' values onto the nine labels.

        The two button gates live here too: ``edit_plate_input``
        starts read-only at the crossing's current plate, and Delete
        is offered only while this crossing is the engine's newest
        (:func:`is_last_crossing`).
        """
        fields = build_fields(self.crossing, self.roster, self.engine)
        self._render_fields(fields)
        self.edit_plate_input.SetValue(fields.plate)
        self.edit_plate_input.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool
        self.edit_btn.Enable(True)  # noqa: FBT003 -- wx API takes a positional bool
        self.delete_btn.Enable(is_last_crossing(self.engine, self.crossing))

    def _on_ok(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx ships no stubs
        """Handle ``wxID_OK``: commit an edited plate, or just close.

        An unchanged plate is not a correction, so OK closes. A
        changed one commits through
        :meth:`~rivercrossing.ride.RideEngine.reassign_crossing`
        with the crossing's *ride-wide* ordinal -- that method's
        ``seq`` is the position in ``engine.crossings``, deliberately
        not the per-entry lap number (``ride.py``'s own E7.1.1 note)
        -- and closes. A refusal (a blank or unknown plate, a ride that
        is not RUNNING or REOPENED) lands on the info bar and leaves
        the dialog open.

        *event* is never skipped: this handler alone decides whether
        the dialog closes (``ride_setup._on_ok``'s measured note).
        """
        new_plate = self.edit_plate_input.GetValue().strip()
        current = self.crossing.rider_plate or self.crossing.entry_id
        if new_plate == current:
            self.dialog.EndModal(wx.ID_OK)
            return
        if not new_plate:
            self.show_refusal("Enter a plate number to reassign this crossing.")
            return
        ordinal = self._ordinal()
        if ordinal is None:
            self.show_refusal("Could not reassign: this crossing is no longer recorded.")
            return
        try:
            self.engine.reassign_crossing(ordinal, new_plate, reason=EDIT_REASON)
        except RideEngineError as exc:
            self.show_refusal(f"Could not reassign: {exc}")
            return
        self.dialog.EndModal(wx.ID_OK)

    def _ordinal(self) -> int | None:
        """Return the crossing's 1-based ride-wide ordinal, or ``None``.

        Read off the engine's own record order -- the index
        ``reassign_crossing`` expects, which is *not* ``Crossing.seq``.
        ``None`` means the engine no longer holds this crossing, so
        there is nothing to reassign.
        """
        for index, candidate in enumerate(self.engine.crossings):
            if candidate is self.crossing:
                return index + 1
        return None

    def _on_delete(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``delete_btn``: the console's Undo, confirmed.

        Delete is offered only for the newest crossing
        (:func:`is_last_crossing`), so the danger confirm names that
        one crossing and a confirmed OK runs
        :meth:`~rivercrossing.ride.RideEngine.undo_last` -- the same
        operation the console's Undo button runs -- then closes. A
        refusal (a ride that is not RUNNING or REOPENED) lands on the
        info bar and leaves the dialog open.
        """
        event.Skip()
        fields = build_fields(self.crossing, self.roster, self.engine)
        if std_dialogs.show_danger(
            self.dialog,
            _DELETE_TITLE,
            delete_message(fields),
            _OK_LABEL,
            _CANCEL_LABEL,
        ) != int(wx.ID_OK):
            return
        try:
            self.engine.undo_last()
        except RideEngineError as exc:
            self.show_refusal(f"Undo unavailable: {exc}")
            return
        self.dialog.EndModal(wx.ID_OK)


class MissDetailView(_DetailDialogView):
    """Code-side behaviour for the dialog in miss mode (K2).

    Opened on a pending miss feed row: it renders
    :func:`build_miss_fields` once at construction and offers exactly
    one action -- score the miss. OK assigns the typed plate through
    :meth:`~rivercrossing.ride.RideEngine.assign_plate_to_miss`, which
    records the crossing and deals its card at the miss's own instant.
    Delete is not bound: the button stays disabled, because a miss is
    not ``engine.crossings[-1]`` and cannot be ``undo_last``.
    """

    def __init__(self, dialog: wx.Dialog, *, miss: PendingMiss, engine: RideEngine) -> None:
        """Decorate an already-loaded dialog for *miss*.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap loaded from
                ``dialogs.xrc``.
            miss: The pending miss this dialog scores.
            engine: The ride's live engine -- the read side of the
                miss's instant and the write side of the assignment.
        """
        super().__init__(dialog, engine)
        self.miss = miss

        # OK alone decides whether the dialog closes, without a Skip
        # (ride_setup._on_ok's measured note): a Skip would let wx's
        # stock OK close the dialog before the commit ran.
        self.dialog.Bind(wx.EVT_BUTTON, self._on_ok, self.ok_btn)

        self.render()

    def render(self) -> None:
        """Write :func:`build_miss_fields` onto the labels and gates.

        The plate field starts blank and read-only (there is no current
        plate to show); Edit unlocks it. Delete is disabled -- a miss
        has no ``undo_last``.
        """
        self._render_fields(build_miss_fields(self.miss))
        self.edit_plate_input.SetValue("")
        self.edit_plate_input.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool
        self.edit_btn.Enable(True)  # noqa: FBT003 -- wx API takes a positional bool
        self.delete_btn.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool

    def _on_ok(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx ships no stubs
        """Handle ``wxID_OK``: assign the typed plate to the miss.

        A blank field refuses (there is no plate to keep, unlike the
        crossing mode's unchanged-plate close). A non-blank one commits
        through
        :meth:`~rivercrossing.ride.RideEngine.assign_plate_to_miss`
        and closes; a refusal (an unknown plate, a ride that is not
        RUNNING or REOPENED) lands on the info bar and leaves the
        dialog open.

        *event* is never skipped: this handler alone decides whether
        the dialog closes (``ride_setup._on_ok``'s measured note).
        """
        new_plate = self.edit_plate_input.GetValue().strip()
        if not new_plate:
            self.show_refusal("Enter a plate number to assign to this miss.")
            return
        try:
            self.engine.assign_plate_to_miss(
                self.miss.miss_seq, new_plate, reason=MISS_EDIT_REASON
            )
        except RideEngineError as exc:
            self.show_refusal(f"Could not assign: {exc}")
            return
        self.dialog.EndModal(wx.ID_OK)
