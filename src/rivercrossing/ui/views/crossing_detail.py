# SPDX-License-Identifier: GPL-3.0-only
"""Crossing Detail: the console feed's row dialog (J2), two modes (K2).

Double-clicking a row in the console's ``crossings_list`` opens
``crossing_detail_dlg`` (``dialogs.xrc``) on that row. A **crossing**
row shows every fact the feed compresses into seven columns -- the
rider the typed plate belongs to, the entry's team, the plate, the lap
number, the crossing/lap/total times, the dealt card's glyph and the
crossing's Status -- the card's disposition, or Duplicate for one half
of a double-entry pair -- plus the corrections the engine already owns,
all rendered into the nine read-only entry boxes ``crossing_*_lbl``
(G2: four columns in one "Details" group). The plate is read-only copy
(``crossing_plate_lbl``; the dialog has no editable field), so Edit
opens the ``crossing_number_dlg``
Save/Cancel prompt (:func:`run_plate_dialog`) and commits the plate
through
:meth:`~rivercrossing.ride.RideEngine.reassign_crossing` -- the
console's own "wrong plate on the line" correction. Phase 2 adds Edit
Time, which opens ``edit_crossing_dlg`` in edit mode through
:func:`~rivercrossing.ui.views.corrections.run_edit_crossing` and
commits :meth:`~rivercrossing.ride.RideEngine.edit_crossing`, and Void
Card, which opens ``void_card_confirm_dlg`` through
:func:`~rivercrossing.ui.views.corrections.run_void_card` and voids the
crossing's own **credited** card -- or a held one on a duplicate
crossing, whose twin the Status field tells the operator to delete (a
held card on a lone crossing stays the review surface's). Both were
the retired Reassign Plate…/Void Card… menu rows' one real home.

Every correction commits through the engine and then **returns to this
dialog**, re-rendered in place on the crossing the engine now holds, so
a scorer can retime, replate and void one crossing without reopening
the row: OK is the one control that closes it. Delete is the single
correction that still closes -- its crossing is gone -- and, for any
crossing of a live (RUNNING or REOPENED) ride, removes the one it shows
after a danger confirm: the newest runs
:meth:`~rivercrossing.ride.RideEngine.undo_last`, exactly the console's
Undo button, and any other one runs
:meth:`~rivercrossing.ride.RideEngine.void_crossing` with
:data:`DELETE_REASON`, voiding its card and renumbering the entry's
later laps.

A **miss** row (a pending miss, K) opens the same dialog in miss mode:
:class:`MissDetailView` renders the placeholders the feed row itself
shows (Plate ``-``, Name ``missed``). Its Edit opens the same Plate
prompt, blank (a miss has no plate to correct), and commits the number
it saves through
:meth:`~rivercrossing.ride.RideEngine.assign_plate_to_miss`, which
records the crossing and deals its card at the miss's own instant. The
dialog then **stays open** and becomes that crossing's own detail --
full crossing mode, so Edit now reassigns its plate and Edit Time, Void
Card and Delete are offered -- never a close: scoring a miss is not an
exit. A blank field and an assign the engine refuses both leave the
dialog open with the refusal shown. Delete is not offered while the
miss stands: a miss is not ``engine.crossings[-1]`` and cannot be
undone.

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
from typing import TYPE_CHECKING, Any, cast

import wx
import wx.xrc  # submodule, not loaded by plain `import wx`

from rivercrossing.ride import RideEngineError, RideStatus
from rivercrossing.roster import EntryType
from rivercrossing.ui import ids, std_dialogs
from rivercrossing.ui.card_text import format_card
from rivercrossing.ui.presenters.data_source import format_duration
from rivercrossing.ui.rider_columns import SOLO_TEAM_TEXT
from rivercrossing.ui.views import corrections, dialogs
from rivercrossing.ui.views._support import (
    DialogFindMixin,
    clamp_to_display,
    find_control,
    load_dialog,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from rivercrossing.ride import Crossing, PendingMiss, RideEngine
    from rivercrossing.roster import Entry, Roster

__all__ = [
    "CROSSING_DETAIL_INFOBAR",
    "DELETE_REASON",
    "EDIT_REASON",
    "MISS_EDIT_REASON",
    "CrossingDetailFields",
    "CrossingDetailView",
    "MissDetailView",
    "build_fields",
    "build_miss_fields",
    "confirm_delete_crossing",
    "delete_message",
    "is_last_crossing",
    "reassign_crossing_plate",
    "run_plate_dialog",
]

# ui/ids.py is generated from the .xrc files (R-05);
# crossing_detail_infobar never appears there since XRC cannot author a
# wxInfoBar at all (rider_issues.py's ISSUES_INFOBAR precedent).
CROSSING_DETAIL_INFOBAR = "crossing_detail_infobar"

# The audited reason OK's reassign carries: reassign_crossing refuses a
# blank one and the audit trail shows it verbatim.
EDIT_REASON = "crossing detail edit"

# The audited reason Delete carries when it voids a crossing that is
# not the newest: undo_last's restitution is impossible for one with
# later laps, so the specific-crossing void is the correction.
DELETE_REASON = "crossing detail delete"

# The audited reason the miss mode's Edit carries (K2), the crossing
# EDIT_REASON's own counterpart.
MISS_EDIT_REASON = "miss detail edit"

# The height the code-side info bar reserves on top of the dialog's own
# fitted size, used when wx has measured no best size for the hidden bar
# yet (see _DetailDialogView._apply_min_size).
_INFOBAR_ALLOWANCE = 40

# The refusal a correction whose crossing the engine no longer holds
# shows; reassign_crossing_plate's own text, shared so Edit (the ordinal
# lookup) and Edit Time cannot drift apart.
_STALE_CROSSING = "this crossing is no longer recorded."

# The Status field's four values. Duplicate is Phase 3's double-entry
# state (the crossing is one half of a live pair, see _is_duplicate)
# and takes precedence over the card's own disposition: the operator's
# next action is to delete one of the two twins. Of the rest, Held is
# R-34's short-lap hold-for-review state, and a card neither held nor
# credited was voided out of the ride entirely (RideEngine.void_held).
_DUPLICATE_STATUS = "Duplicate"
_HELD_STATUS = "Held - Review"
_CREDITED_STATUS = "Credited"
_VOIDED_STATUS = "Void"

_LAP_SECONDS_PER_HOUR = 3600

_DELETE_TITLE = "Undo Last Crossing?"
# The non-newest confirm deletes the crossing outright, so both its
# title and its OK label read "Delete" (Phase 2 reworded them from the
# old "Void" copy).
_VOID_TITLE = "Delete Crossing?"

_OK_LABEL = "Undo"
_VOID_LABEL = "Delete"
_CANCEL_LABEL = "Cancel"


@dataclass(frozen=True, slots=True)
class CrossingDetailFields:
    """The read-only value each ``crossing_detail_dlg`` box renders.

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


def _team_name(entry: Entry | None, entry_id: str) -> str:
    """Return the Team field's text for *entry*.

    A solo rider has no team to name, so the field reads the word
    :data:`~rivercrossing.ui.rider_columns.SOLO_TEAM_TEXT` rather than
    repeating the rider's own name from the Rider field -- the word
    the console feed's Team column shows for the same crossing. A
    crossing whose entry has left the roster has nothing to type-check,
    so it falls back to the raw *entry_id* (``build_fields``' own
    ``entry_name`` rule).
    """
    if entry is None:
        return entry_id
    if entry.type is EntryType.SOLO:
        return SOLO_TEAM_TEXT
    return entry.display_name


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


def _is_duplicate(engine: RideEngine, crossing: Crossing) -> bool:
    """Return whether *crossing* is one half of a live duplicate pair.

    Phase 3's double-entry detector: two crossings of one entry at the
    identical instant (``engine.duplicate_crossings``). Identity, not
    equality -- the engine hands out the very ``Crossing`` objects it
    recorded, the rule :func:`is_last_crossing` reads.
    """
    return any(crossing is twin for pair in engine.duplicate_crossings() for twin in pair)


def _held_status(engine: RideEngine, crossing: Crossing, held: object | None) -> str:
    """Return the card's disposition text for *crossing*.

    A duplicate crossing (one half of a live pair) reports
    :data:`_DUPLICATE_STATUS` first, whatever its card is doing: the
    row the operator needs corrected is the crossing, not the card.
    Otherwise three states, all read off the engine: the card is held
    for review (R-34 -- *held* is :meth:`RideEngine.held_card_for`'s
    answer), it was voided out of the ride entirely, or it is credited
    to the entry's hand. Voided is read from
    :meth:`RideEngine.is_card_voided` on this crossing's own dealt
    object -- the identity-keyed registry, never membership of the
    credited hand, which matches by value and would let a same-code
    sibling report a retired card as "Credited". The hand's own
    membership is then the last reading: a card it no longer holds
    (a value-matched twin, say) is voided too. The distinction
    matters in exactly the dialog a scorer opens to check why a card is
    missing from a hand.
    """
    if _is_duplicate(engine, crossing):
        return _DUPLICATE_STATUS
    if held is not None:
        return _HELD_STATUS
    card = engine.card_for(crossing)
    if engine.is_card_voided(card):
        return _VOIDED_STATUS
    if card in engine.credited_cards(crossing.entry_id):
        return _CREDITED_STATUS
    return _VOIDED_STATUS


def build_fields(crossing: Crossing, roster: Roster, engine: RideEngine) -> CrossingDetailFields:
    """Return the read-only field values for *crossing*.

    The whole view-model, built from the live domain objects: the
    roster resolves which entry the crossing belongs to, the engine
    answers the timing and card questions. Pure -- no ``wx`` -- so the
    mapping is pinned headlessly.

    Change D3: a voided card's Card field reads :data:`_VOIDED_STATUS`
    rather than its glyph. The card is out of the ride -- in neither
    the hold queue nor the entry's hand -- so its glyph would name a
    card the entry does not hold. A held, credited or duplicate card
    keeps the real dealt code's glyph, the feed's own rule.
    """
    entry = roster.resolve_plate(crossing.entry_id)
    entry_name = entry.display_name if entry is not None else crossing.entry_id
    rider = _rider_name(entry, crossing.rider_plate)
    team = _team_name(entry, crossing.entry_id)
    lap_time, total = _lap_and_total(engine, crossing)
    # W9's feed rule: a held crossing still shows the real dealt code,
    # never a placeholder -- held_card_for is that answer.
    held = engine.held_card_for(crossing)
    card = held if held is not None else engine.card_for(crossing)
    status = _held_status(engine, crossing, held)
    return CrossingDetailFields(
        rider=rider or entry_name,
        team=team,
        plate=crossing.rider_plate or crossing.entry_id,
        lap=str(crossing.seq),
        time=_local_time(crossing.crossed_at),
        lap_time=_format_lap_time(lap_time),
        total=format_duration(total),
        card=_VOIDED_STATUS if status == _VOIDED_STATUS else format_card(card.code()),
        held=status,
    )


def build_miss_fields(miss: PendingMiss) -> CrossingDetailFields:
    """Return the read-only field values for a pending *miss* (K2).

    A miss has no entry, lap or card, so only the instant the operator
    signalled it is known; every other cell renders the placeholder the
    feed's own ``-``/``missed`` row uses. The plate a scorer types is
    not one of these boxes: Edit opens the Plate prompt and commits its
    answer through ``assign_plate_to_miss``, which records the crossing
    the boxes never described. Pure -- no ``wx`` -- so the mapping is
    pinned headlessly.
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

    Delete's command selector: ``undo_last`` removes exactly one
    crossing -- the newest -- so that crossing gets the console's own
    Undo and every other one the specific-crossing void. Identity, not
    equality: the app hands the view the very object it read out of
    ``engine.crossings``.
    """
    crossings = engine.crossings
    return bool(crossings) and crossings[-1] is crossing


def reassign_crossing_plate(engine: RideEngine, crossing: Crossing, new_plate: str) -> str | None:
    """Move *crossing* to *new_plate*; return a refusal, or ``None``.

    The shared commit of the Crossing Detail dialog's two plate paths
    (OK, on the field the dialog owns, and Edit, on the Plate prompt's
    answer) and of the console feed's own Ctrl+E: the crossing's
    1-based ride-wide ordinal -- its position in ``engine.crossings``,
    deliberately *not* ``Crossing.seq`` (``ride.py``'s own E7.1.1
    note) -- goes to
    :meth:`~rivercrossing.ride.RideEngine.reassign_crossing`, which
    resolves *new_plate* and owns the blank/unknown-plate and
    ride-state refusals.

    Returns:
        ``None`` when the reassign committed, otherwise the operator-
        facing refusal text (the caller shows it on its own surface --
        the dialog's info bar, the console's status bar).
    """
    ordinal = next(
        (index + 1 for index, candidate in enumerate(engine.crossings) if candidate is crossing),
        None,
    )
    if ordinal is None:
        return f"Could not reassign: {_STALE_CROSSING}"
    try:
        engine.reassign_crossing(ordinal, new_plate, reason=EDIT_REASON)
    except RideEngineError as exc:
        return f"Could not reassign: {exc}"
    return None


def delete_message(fields: CrossingDetailFields, *, newest: bool) -> str:
    """Return Delete's danger-confirm question for *fields*.

    UX-DESKTOP §4: a destructive confirm names the object it is about
    to destroy, so the question carries the crossing's own time, rider
    and lap rather than a bare "Are you sure?". *newest* picks the
    removal's own wording: ``undo_last`` removes the newest crossing
    and restitutes its card, while the specific-crossing void keeps the
    card out of the ride and renumbers the entry's later laps.
    """
    crossing = f"{fields.time} · {fields.rider} · lap {fields.lap}"
    if newest:
        return f"Undo crossing {crossing}? The newest crossing and its dealt card are removed."
    return (
        f"Delete crossing {crossing}? The crossing and its card are voided; "
        "the entry's later laps renumber."
    )


def confirm_delete_crossing(  # noqa: PLR0913, PLR0917 -- (parent, crossing, roster, engine)
    parent: wx.Window | None,
    crossing: Crossing,
    roster: Roster,
    engine: RideEngine,
    *,
    on_refusal: Callable[[str], None],
) -> bool:
    """Confirm and remove *crossing*; report whether it was removed.

    The shared Delete of the Crossing Detail dialog's ``delete_btn`` and
    the console feed's own Delete (feed-scoped) and Ctrl+D (frame
    accelerator). §5: any crossing of a live ride is deletable, and
    which engine command runs depends
    on *which* crossing it is (:func:`is_last_crossing`). The newest one
    runs :meth:`~rivercrossing.ride.RideEngine.undo_last` -- the same
    operation the console's Undo button runs, its card restituted -- and
    any other one runs
    :meth:`~rivercrossing.ride.RideEngine.void_crossing` with
    :data:`DELETE_REASON`, which voids the card and renumbers the
    entry's later laps. The danger confirm names the crossing either
    way, with the matching title and question (:func:`delete_message`).

    Args:
        parent: The window the confirm opens over.
        crossing: The live crossing to remove.
        roster: The ride's in-memory roster, for the confirm's own
            rider/team naming.
        engine: The ride's live engine -- the read side of the naming,
            the write side of the removal.
        on_refusal: Called with the operator-facing text when the
            engine refuses (a ride that is not RUNNING or REOPENED);
            the caller shows it on its own surface.

    Returns:
        ``True`` when the crossing was removed, ``False`` on cancel or
        refusal.
    """
    fields = build_fields(crossing, roster, engine)
    newest = is_last_crossing(engine, crossing)
    if std_dialogs.show_danger(
        parent,
        _DELETE_TITLE if newest else _VOID_TITLE,
        delete_message(fields, newest=newest),
        _OK_LABEL if newest else _VOID_LABEL,
        _CANCEL_LABEL,
    ) != int(wx.ID_OK):
        return False
    try:
        if newest:
            engine.undo_last()
        else:
            engine.void_crossing(crossing.entry_id, crossing.seq, reason=DELETE_REASON)
    except RideEngineError as exc:
        refusal = "Undo unavailable" if newest else "Could not void"
        on_refusal(f"{refusal}: {exc}")
        return False
    return True


def run_plate_dialog(resource: wx.xrc.XmlResource, *, opener: wx.Dialog, plate: str) -> str | None:
    """Prompt for a plate over *opener*; return it, or ``None``.

    §9's Save/Cancel prompt for the crossing-mode Edit action: loads
    ``crossing_number_dlg`` (:data:`~rivercrossing.ui.ids.
    CROSSING_NUMBER_DLG`) from *resource*, seeds its four-digit
    ``number_input`` with *plate*, and shows it modally through
    :func:`~rivercrossing.ui.views.dialogs.run_dialog` -- the one seam
    every dialog in this codebase shows through. Save returns the
    field's trimmed text; Cancel -- and Escape, which the stock
    ``wxID_CANCEL`` already routes -- returns ``None``.

    Nothing here resolves or validates the plate: the caller hands it
    to :meth:`~rivercrossing.ride.RideEngine.reassign_crossing`, which
    owns the blank/unknown-plate refusal (the §9 rule), so this prompt
    never duplicates the roster's own rules.

    Args:
        resource: The app's loaded ``wx.xrc.XmlResource``.
        opener: The dialog the prompt opens over, and the window
            ``run_dialog`` returns focus to once it ends.
        plate: The crossing's current plate, shown ready to retype.

    Returns:
        The plate the operator saved, trimmed, or ``None`` on cancel
        (or when no ``crossing_number_dlg`` is authored).
    """
    window = load_dialog(resource, ids.CROSSING_NUMBER_DLG)
    if window is None:
        return None
    try:
        number_input = find_control(window, ids.NUMBER_INPUT, wx.TextCtrl)
        number_input.SetValue(plate)
        number_input.SetFocus()
        number_input.SelectAll()
        if dialogs.run_dialog(window, opener=opener) != int(wx.ID_OK):
            return None
        return str(number_input.GetValue().strip())
    finally:
        if not window.IsBeingDeleted():
            window.Destroy()


class _DetailDialogView(DialogFindMixin):  # _find: ui.views._support
    """Widget wiring shared by both modes of ``crossing_detail_dlg``.

    The crossing mode (:class:`CrossingDetailView`) and the miss mode
    (:class:`MissDetailView`) decorate the same frozen dialog, so the
    nine value boxes, the four correction buttons, the stock OK, the
    code-side info bar and Escape all live here -- and so does every
    correction, because the two modes are one dialog that can change
    mode: scoring a miss records a crossing, which this view then shows
    in full crossing mode (:meth:`_on_edit`). A subclass supplies only
    what its mode opens with, the view-model it renders
    (:meth:`_render_crossing` / :meth:`_render_miss`, through its own
    one-line ``render``). Binding every button here, once, is what keeps
    a duplicate ``wx.Bind`` from delivering an event twice. OK itself is
    :meth:`_on_ok` -- the one ordinary control that closes the window --
    so no subclass overrides it.
    """

    # The crossing this dialog shows, or ``None`` while it is in miss
    # mode. A class-level default, not an ``__init__``-only attribute:
    # the white-box tests build views with ``object.__new__`` and never
    # run ``__init__`` (test_crossing_detail._view / _miss_view).
    crossing: Crossing | None = None

    # The pending miss this dialog scores, set by MissDetailView's own
    # ``__init__``. Read only while ``crossing`` is ``None``, and
    # dropped by the hand-off that scores it. Declared here because
    # :meth:`_score_miss` and :meth:`_render_miss` are the base's.
    miss: PendingMiss

    # The ride's in-memory roster, set by both modes' ``__init__``: the
    # read side of every correction's own naming, as the base's
    # handlers do it.
    roster: Roster

    def __init__(self, dialog: wx.Dialog, engine: RideEngine) -> None:
        """Find the frozen controls, build the info bar and lay it out.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap loaded from
                ``dialogs.xrc``.
            engine: The ride's live engine -- the read side of every
                field and the write side of every correction.
        """
        self.dialog = dialog
        self.engine = engine

        self.crossing_rider_lbl = self._find(ids.CROSSING_RIDER_LBL, wx.TextCtrl)
        self.crossing_team_lbl = self._find(ids.CROSSING_TEAM_LBL, wx.TextCtrl)
        self.crossing_plate_lbl = self._find(ids.CROSSING_PLATE_LBL, wx.TextCtrl)
        self.crossing_lap_lbl = self._find(ids.CROSSING_LAP_LBL, wx.TextCtrl)
        self.crossing_time_lbl = self._find(ids.CROSSING_TIME_LBL, wx.TextCtrl)
        self.crossing_lap_time_lbl = self._find(ids.CROSSING_LAP_TIME_LBL, wx.TextCtrl)
        self.crossing_total_lbl = self._find(ids.CROSSING_TOTAL_LBL, wx.TextCtrl)
        self.crossing_card_lbl = self._find(ids.CROSSING_CARD_LBL, wx.TextCtrl)
        self.crossing_held_lbl = self._find(ids.CROSSING_HELD_LBL, wx.TextCtrl)
        self.edit_btn = self._find(ids.EDIT_BTN, wx.Button)
        self.edit_time_btn = self._find(ids.EDIT_TIME_BTN, wx.Button)
        self.void_card_btn = self._find(ids.VOID_CARD_BTN, wx.Button)
        self.delete_btn = self._find(ids.DELETE_BTN, wx.Button)
        self.ok_btn = self._find(dialogs.WX_ID_OK, wx.Button)

        self.crossing_detail_infobar = self._build_infobar()
        self._apply_min_size()

        # Each button is bound here, once, whatever the mode: one
        # ``Bind`` per control, because a duplicate binding delivers
        # every later event twice (view/main_frame.py's measured
        # duplicate-Bind note) -- and the subclasses' own ``ok_btn``
        # binding, which they used to add, would fire ``EndModal``
        # twice now that the base binds it.
        #
        # ``_on_ok`` never Skips: it alone decides whether the dialog
        # closes (ride_setup._on_ok's measured note), and a Skip would
        # let wx's stock OK close the window before the handler ran.
        # The corrections do Skip, so nothing else about their events is
        # swallowed.
        self.dialog.Bind(wx.EVT_BUTTON, self._on_ok, self.ok_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_edit, self.edit_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_edit_time, self.edit_time_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_void_card, self.void_card_btn)
        self.dialog.Bind(wx.EVT_BUTTON, self._on_delete, self.delete_btn)

        # Escape points at OK: the window authors no wxID_CANCEL, and
        # wx's own Escape handling only ever looks for that id
        # (measured -- dialogs.wire_close_button's docstring), so
        # without this the dialog would trap a keyboard-only operator.
        # Nothing is staged in this dialog -- every correction commits
        # through its own button -- so Escape discards nothing and is
        # never the destructive path (UX-DESKTOP §3).
        self.dialog.SetEscapeId(self.ok_btn.GetId())

    def _apply_min_size(self) -> None:
        """Floor the height so the info bar never clips the content.

        ``Fit()`` first, so the size being floored is whatever this
        platform measured for the built window; the hidden info bar
        contributes no height to that measurement, so its own best
        height (``_INFOBAR_ALLOWANCE`` when wx has measured none) is
        added back before the total is clamped to the display's work
        area (:func:`~rivercrossing.ui.views._support.
        clamp_to_display`), so a small screen still gets a fully visible
        dialog. The clamped size is both the floor (``SetMinSize``) and
        the size the dialog opens at (``SetSize``): a floor alone would
        still let the loaded window keep the size XRC gave it
        (rider_issues.py's own idiom).
        """
        self.dialog.Fit()
        fitted = self.dialog.GetSize()
        allowance = self.crossing_detail_infobar.GetBestSize().height or _INFOBAR_ALLOWANCE
        width, height = clamp_to_display(fitted.width, fitted.height + allowance)
        self.dialog.SetMinSize(wx.Size(width, height))
        self.dialog.SetSize(wx.Size(width, height))

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
        """Write *fields* onto the nine read-only entry boxes."""
        self.crossing_rider_lbl.SetValue(fields.rider)
        self.crossing_team_lbl.SetValue(fields.team)
        self.crossing_plate_lbl.SetValue(fields.plate)
        self.crossing_lap_lbl.SetValue(fields.lap)
        self.crossing_time_lbl.SetValue(fields.time)
        self.crossing_lap_time_lbl.SetValue(fields.lap_time)
        self.crossing_total_lbl.SetValue(fields.total)
        self.crossing_card_lbl.SetValue(fields.card)
        self.crossing_held_lbl.SetValue(fields.held)

    def _render_crossing(self) -> None:
        """Write :func:`build_fields`' values onto the nine entry boxes.

        The crossing mode's whole render, and the one a scored miss is
        handed over to. The button gates live here too: Edit (the Plate
        prompt) and Edit Time are always offered; Delete -- either
        correction, whichever this crossing is -- only while the ride is
        RUNNING or REOPENED; and Void Card when this crossing's own card
        is **credited** (a held card is the review surface's, a voided
        one is already out of the ride) or when this crossing is one
        half of a duplicate pair, whose held card is voidable here
        because deleting one twin is the correction it needs (Phase 3).
        """
        fields = build_fields(self._shown_crossing(), self.roster, self.engine)
        self._render_fields(fields)
        self.edit_btn.Enable(True)  # noqa: FBT003 -- wx API takes a positional bool
        self.edit_time_btn.Enable(True)  # noqa: FBT003 -- wx API takes a positional bool
        self.void_card_btn.Enable(self._card_is_credited() or self._is_duplicate())
        self.delete_btn.Enable(self.engine.state in (RideStatus.RUNNING, RideStatus.REOPENED))

    def _render_miss(self) -> None:
        """Write :func:`build_miss_fields` onto the boxes and gates.

        Edit -- the Plate prompt the typed number comes from -- is the
        miss's one action. Delete, Edit Time and Void Card are all
        disabled: a miss has no ``undo_last``, no clock time and no
        card, and each handler behind them reads :attr:`crossing` (the
        XRC authors the three buttons enabled, so this gate is what
        keeps a miss's window safe).
        """
        self._render_fields(build_miss_fields(self.miss))
        self.edit_btn.Enable(True)  # noqa: FBT003 -- wx API takes a positional bool
        self.edit_time_btn.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool
        self.void_card_btn.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool
        self.delete_btn.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool

    def _shown_crossing(self) -> Crossing:
        """Return the crossing this dialog shows.

        The crossing-only paths' one read of :attr:`crossing`. A
        miss-mode view holds none until its Edit scores the miss and the
        hand-off fills one in -- which is exactly when those paths
        become reachable -- so the cast only narrows the optional
        attribute for mypy.
        """
        return cast("Crossing", self.crossing)

    def show_refusal(self, message: str) -> None:
        """Show *message* on :data:`CROSSING_DETAIL_INFOBAR`."""
        self.crossing_detail_infobar.ShowMessage(message, wx.ICON_WARNING)
        self.dialog.Layout()

    def _on_ok(self, event: Any) -> None:  # noqa: ANN401, ARG002 -- wx ships no stubs
        """Handle ``wxID_OK``: close the dialog.

        OK is the window's one control that closes it, in both modes,
        and it never refuses: every correction commits through its own
        button (the miss's, since Phase 3, through Edit), so there is no
        staged field for OK to read.

        *event* is never skipped: this handler alone decides whether
        the dialog closes (``ride_setup._on_ok``'s measured note).
        """
        self.dialog.EndModal(wx.ID_OK)

    def _on_edit(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``edit_btn``: prompt for a plate, then commit it.

        One button, two commits, chosen by :attr:`crossing`. With a
        crossing, §9's Save/Cancel Plate prompt
        (:func:`run_plate_dialog`) opens prefilled with the plate this
        dialog shows (its own plate is read-only copy); Save hands the
        typed plate straight to
        :meth:`~rivercrossing.ride.RideEngine.reassign_crossing` -- the
        engine resolves the plate and refuses a blank or unknown one --
        and a committed reassign re-renders this dialog on the moved
        crossing. With none, the dialog is in miss mode and the prompt
        opens blank: the number it saves is the miss's own commit
        (:meth:`_score_miss`). Either way the dialog stays open, a
        refusal lands on :data:`CROSSING_DETAIL_INFOBAR`, and Cancel
        does nothing.
        """
        event.Skip()
        crossing = self.crossing
        if crossing is None:
            self._score_miss()
            return
        new_plate = run_plate_dialog(
            wx.xrc.XmlResource.Get(),
            opener=self.dialog,
            plate=crossing.rider_plate or crossing.entry_id,
        )
        if new_plate is None:
            return
        self._commit_plate(new_plate)

    def _score_miss(self) -> None:
        """Score the pending miss at :attr:`miss` onto the typed plate.

        The miss mode's one action: a miss has no plate to correct, only
        one to enter, so the §9 prompt opens blank and the number it
        saves goes to
        :meth:`~rivercrossing.ride.RideEngine.assign_plate_to_miss`,
        which records the crossing and deals its card at the miss's own
        instant. The dialog then **stays open** as that crossing's own
        detail -- :attr:`crossing` takes the record's last entry, the
        assignment being an append, and the miss is dropped -- so the
        scorer keeps working on the crossing instead of losing the
        window. A blank field stays open with its own refusal, as does
        an assign the engine refuses (an unknown plate, a ride that is
        not RUNNING or REOPENED); Cancel does nothing.
        """
        new_plate = run_plate_dialog(wx.xrc.XmlResource.Get(), opener=self.dialog, plate="")
        if new_plate is None:
            return
        if not new_plate:
            # assign_plate_to_miss would refuse this as an unknown
            # plate; the operator is told what to do instead.
            self.show_refusal("Enter a plate to assign to this miss.")
            return
        try:
            self.engine.assign_plate_to_miss(
                self.miss.miss_seq, new_plate, reason=MISS_EDIT_REASON
            )
        except RideEngineError as exc:
            self.show_refusal(f"Could not assign: {exc}")
            return
        self.crossing = self.engine.crossings[-1]
        del self.miss
        self._render_crossing()

    def _commit_plate(self, new_plate: str) -> bool:
        """Commit *new_plate* as this crossing's plate; report success.

        :func:`reassign_crossing_plate` owns the ordinal arithmetic and
        the engine's own refusals (a blank or unknown plate, a ride that
        is not RUNNING or REOPENED, a crossing the engine no longer
        holds); a non-``None`` answer lands on
        :data:`CROSSING_DETAIL_INFOBAR` and reports ``False``, leaving
        the dialog as it was. On success the view re-points at the
        crossing the reassign moved -- ``Crossing`` is frozen, so the
        engine's replacement is the live one, and ``reassign_crossing``
        removes then appends, so it is the record's **last** entry: a
        plate/instant lookup would instead find the older twin of a
        documented duplicate pair -- and re-renders it in place.
        """
        refusal = reassign_crossing_plate(self.engine, self._shown_crossing(), new_plate)
        if refusal is not None:
            self.show_refusal(refusal)
            return False
        self.crossing = self.engine.crossings[-1]
        self._render_crossing()
        return True

    def _is_duplicate(self) -> bool:
        """Return whether this crossing is one half of a duplicate pair.

        Void Card's second gate (Phase 3): a duplicate is shown
        :data:`_DUPLICATE_STATUS` precisely so the operator deletes one
        of its two twins, so a **held** card on one must stay voidable
        here (a credited one already is, via :meth:`_card_is_credited`).
        """
        return _is_duplicate(self.engine, self._shown_crossing())

    def _card_is_credited(self) -> bool:
        """Return whether this crossing's dealt card is credited.

        The Void Card gate: a held card (R-34) is the review surface's
        domain and a voided card is already out of the ride, so only a
        card in the entry's credited hand is voidable here.
        """
        crossing = self._shown_crossing()
        held = self.engine.held_card_for(crossing)
        credited = self.engine.card_for(crossing) in self.engine.credited_cards(crossing.entry_id)
        return held is None and credited

    def _on_edit_time(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``edit_time_btn``: retime this crossing (Phase 2).

        Opens ``edit_crossing_dlg`` in EDIT mode through the shared
        :func:`~rivercrossing.ui.views.corrections.run_edit_crossing`
        runner, prefilled with this crossing's plate, seq and current
        ``HH:MM:SS`` time on the ride's event date and locked to that
        plate (``read_only_plate``; there is nothing to replate here)
        with its ``void_btn`` suppressed (``suppress_void``: Delete is
        this dialog's own void). A confirmed Save calls
        :meth:`~rivercrossing.ride.RideEngine.edit_crossing` and
        re-renders this dialog on the retimed crossing. A refusal lands
        on :data:`CROSSING_DETAIL_INFOBAR` and leaves the dialog open;
        Cancel does nothing. Both of the engine's refusals are caught:
        :class:`~rivercrossing.ride.RideEngineError` for a ride that is
        not RUNNING or REOPENED, and the ``ValueError``
        ``edit_crossing`` raises for a lap retimed at or before its
        predecessor (Phase 3), which would otherwise escape this wx
        handler as a crash. A crossing the engine no longer holds is
        refused the same way, before either.
        """
        event.Skip()
        crossing = self._shown_crossing()
        edit = corrections.run_edit_crossing(
            wx.xrc.XmlResource.Get(),
            frame=self.dialog,
            adding=False,
            plate=crossing.rider_plate or crossing.entry_id,
            time=_local_time(crossing.crossed_at),
            seq=crossing.seq,
            base_date=self.engine.config.event_date,
            title="Edit Time",
            read_only_plate=True,
            suppress_void=True,
        )
        if edit is None or edit.crossed_at is None:
            # logic-coverage-exempt: T-3 -- suppress_void unbinds the
            # dialog's void button and the reason gate guards OK, so a
            # submission always carries an instant; the guard only
            # narrows CrossingEdit's optional field for mypy.
            return
        seq = edit.seq if edit.seq is not None else crossing.seq
        ordinal = self._ordinal()
        if ordinal is None:
            self.show_refusal(f"Could not edit crossing: {_STALE_CROSSING}")
            return
        try:
            self.engine.edit_crossing(edit.entry_id, seq, edit.crossed_at, edit.reason)
        except (RideEngineError, ValueError) as exc:
            self.show_refusal(f"Could not edit crossing: {exc}")
            return
        self._rerender_at(ordinal)

    def _ordinal(self) -> int | None:
        """Return this crossing's ride-wide ordinal, or ``None``.

        The position this crossing occupies in ``engine.crossings``, the
        identity ``reassign_crossing`` addresses one by. A retime swaps
        its crossing in place (``_replace_crossing``), so the caller
        captures the ordinal before the engine command and can find the
        replacement -- the same slot, a new frozen ``Crossing`` -- after
        it. ``None`` for a crossing the engine no longer holds (a stale
        row): there is no slot to re-render from, and ``next``'s bare
        ``StopIteration`` would escape through the wx handler as a
        crash.
        """
        return next(
            (
                index + 1
                for index, candidate in enumerate(self.engine.crossings)
                if candidate is self.crossing
            ),
            None,
        )

    def _rerender_at(self, ordinal: int) -> None:
        """Point this view at *ordinal*'s crossing and re-render it."""
        self.crossing = self.engine.crossings[ordinal - 1]
        self._render_crossing()

    def _on_void_card(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``void_card_btn``: void this crossing's dealt card.

        Opens ``void_card_confirm_dlg`` through the shared
        :func:`~rivercrossing.ui.views.corrections.run_void_card`
        runner, naming the crossing's own card and entry. A confirmed
        void calls :meth:`~rivercrossing.ride.RideEngine.void_card` and
        re-renders this dialog: the crossing and its lap stay, only the
        card's disposition moved, so the Void Card gate now reads
        disabled. A refusal lands on
        :data:`CROSSING_DETAIL_INFOBAR` and leaves the dialog open;
        Cancel does nothing.

        The engine gets the very object it dealt, not a ``Card`` parsed
        from the confirm dialog's returned code: the registry and the
        hold guard are keyed by identity, so a value-equal copy is a
        different physical card and would slip past the guard.
        """
        event.Skip()
        crossing = self._shown_crossing()
        card = self.engine.card_for(crossing)
        entry = build_fields(crossing, self.roster, self.engine)
        # xrc-windows.md pins this confirm's label to "the entry the
        # card was dealt to" ("45 · J. Okafor"), and fields.team reads
        # "solo" for a solo rider, so the name comes from the rider
        # field -- which falls back to the entry's own name for a
        # team_relay ride and for an entry that left the roster.
        void = corrections.run_void_card(
            wx.xrc.XmlResource.Get(),
            frame=self.dialog,
            entry_id=crossing.entry_id,
            card=card.code(),
            entry=f"{entry.plate} · {entry.rider}",
        )
        if void is None:
            return
        try:
            self.engine.void_card(void.entry_id, card, void.reason)
        except RideEngineError as exc:
            self.show_refusal(f"Could not void card: {exc}")
            return
        self._render_crossing()

    def _on_delete(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Handle ``delete_btn``: remove this crossing, confirmed.

        §5: any crossing of a live ride is deletable, and which engine
        command runs depends on *which* crossing it is
        (:func:`confirm_delete_crossing` is the shared body).
        A refusal (a ride that is not RUNNING or REOPENED) lands on
        the info bar and leaves the dialog open.
        """
        event.Skip()
        if confirm_delete_crossing(
            self.dialog,
            self._shown_crossing(),
            self.roster,
            self.engine,
            on_refusal=self.show_refusal,
        ):
            self.dialog.EndModal(wx.ID_OK)


class CrossingDetailView(_DetailDialogView):
    """Code-side behaviour for ``crossing_detail_dlg`` (J2).

    A read-only detail view with four corrections, not a
    presenter-backed form: it renders :func:`build_fields` at
    construction and forwards its buttons straight to the engine
    (``views/rider_issues.py``'s presenter-inside-the-view shape, minus
    the presenter -- there is no view logic here to put in one). The
    Edit and Delete corrections are the console's own reassign/undo; the
    Phase 2 Edit Time and Void Card buttons reach the same
    ``edit_crossing_dlg`` / ``void_card_confirm_dlg`` runners the
    retired Reassign Plate…/Void Card… menu rows used.

    Every correction re-renders this dialog in place once the engine has
    taken it, so the scorer stays on the crossing and can keep working
    (OK is the one button that closes it; Delete, whose crossing is
    gone, is the other exit).
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
                field and the write side of every correction.
        """
        super().__init__(dialog, engine)
        self.crossing = crossing
        self.roster = roster
        self.render()

    def render(self) -> None:
        """Render this crossing's nine fields and its button gates."""
        self._render_crossing()


class MissDetailView(_DetailDialogView):
    """Code-side behaviour for the dialog in miss mode (K2).

    Opened on a pending miss feed row: it renders
    :func:`build_miss_fields` at construction and offers exactly one
    action -- score the miss. Its Edit opens the same §9 Plate prompt
    the crossing mode uses, blank (a miss has no plate to correct), and
    commits the number it saves through
    :meth:`~rivercrossing.ride.RideEngine.assign_plate_to_miss`, which
    records the crossing and deals its card at the miss's own instant:
    the dialog then **stays open** and re-renders as that crossing's
    detail -- full crossing mode, so Edit now reassigns its plate and
    Edit Time, Void Card and Delete are offered. Delete is not offered
    while the miss stands: the button stays disabled, because a miss is
    not ``engine.crossings[-1]`` and cannot be ``undo_last``.
    """

    def __init__(  # noqa: PLR0913 -- (dialog, miss, roster, engine)
        self,
        dialog: wx.Dialog,
        *,
        miss: PendingMiss,
        roster: Roster,
        engine: RideEngine,
    ) -> None:
        """Decorate an already-loaded dialog for *miss*.

        Args:
            dialog: The ``wx.Dialog`` the app bootstrap loaded from
                ``dialogs.xrc``.
            miss: The pending miss this dialog scores.
            roster: The ride's in-memory roster, read once the miss has
                been scored and the dialog shows the crossing.
            engine: The ride's live engine -- the read side of the
                miss's instant and the write side of the assignment.
        """
        super().__init__(dialog, engine)
        self.miss = miss
        self.roster = roster
        self.render()

    def render(self) -> None:
        """Render the miss's placeholder fields and its button gates."""
        self._render_miss()
