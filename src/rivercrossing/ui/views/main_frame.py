# SPDX-License-Identifier: GPL-3.0-only
"""``main_frame``: the console (1a), wired to its demo/live DataSource.

xrc-windows.md section A's code-side footnote lists six things
``main.xrc`` cannot express: the crossings feed's DataView columns
and per-row attributes, the card imagelist, the three ``wxInfoBar``
shells, the ``main_splitter`` sash restore, per-state menu enabling,
and ``SetAppearance``. This module covers the first four for
``main_frame`` -- per-state menu enabling is ``commands.py``'s route
table (E1.4) and ``SetAppearance`` is ``theme.py``'s job (wired by
the app bootstrap, Phase 8); neither lives here.

:class:`MainFrame` decorates an already-XRC-loaded ``wx.Frame`` -- it
never calls ``LoadFrame`` itself. Loading stays the caller's job
(``harness.load_window`` in tests, the app bootstrap in production),
matching every other window in this codebase and the rule this
repo's own harness states: reuse the one loader, never build a
second.

**Why no separate ``console_panel.py`` (SIMPLECODE Rule 7 -- one
module until a split earns its keep):** module-skeletons.md names
one for "feed, entry field, counters", but ``main.xrc`` never splits
those controls into their own XRC panel resource -- they are plain
children of this one frame, alongside the InfoBars, splitter and
statusbar this module already owns. Two Python files sharing one XRC
window and one set of ``FindWindowByName`` calls would be a
same-window split with no separable XRC boundary behind it, the
paper-cut kind Rule 7 warns against. If a second real window ever
needs the feed-rendering logic, extract it then.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.ride import RideStatus
from rivercrossing.ui import feed_model, ids, sound
from rivercrossing.ui.views._support import default_card_images, find_control

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.cards_imagelist import CardImageList
    from rivercrossing.ui.presenters.console import ConsolePresenter, Cue
    from rivercrossing.ui.presenters.data_source import Counters, DataSource, FeedRow

__all__ = [
    "FINISHED_INFOBAR",
    "MIN_SIZE",
    "REOPENED_INFOBAR",
    "RESUME_INFOBAR",
    "CrossingsFeedModel",
    "MainFrame",
]

_TEXT_ACCESSORS: tuple[Callable[[FeedRow], str], ...] = (
    lambda row: row.time,
    lambda row: row.plate,
    lambda row: row.entry,
    lambda row: str(row.lap),
    lambda row: row.lap_time,
    lambda row: row.total,
)

# ui/ids.py is generated from the .xrc files (R-05); these three
# names never appear there since XRC cannot author a wxInfoBar at
# all (xrc-windows.md's own code-side footnote, main.xrc's header).
RESUME_INFOBAR = "resume_infobar"
REOPENED_INFOBAR = "reopened_infobar"
FINISHED_INFOBAR = "finished_infobar"

# The REOPENED corrections banner (spec §3, R-36): the clock stays
# closed and live plate entry stays off; the operator edits, voids or
# adds crossings, then finishes again. Shown by set_state on REOPENED.
REOPENED_BANNER = (
    "This ride is open for corrections — entry is locked. "
    "Edit, void, or add crossings, then finish again."
)

# xrc-windows.md section A: "Min frame 1100x700, fits 1366x768."
# XRC has no window-level minsize property (main.xrc's own header) --
# only <size>, which sets the *initial* size, not the floor.
MIN_SIZE = (1100, 700)

# How often wire_console's timer drives presenter.tick() (E4.4.1).
# 1 s keeps the clock, counters and R-35's 10 s arm auto-clear honest
# without hammering the DataView with rebuilds.
_TICK_MS = 1000

# Interim, process-lifetime sash memory. Task E8.1.1 ("all settings
# survive relaunch") replaces this with the real, disk-backed
# settings store; nothing else in this module depends on how that
# eventually works, and nothing outside this module reads it.
_persisted_main_splitter_sash: int | None = None


class CrossingsFeedModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``FeedRow`` rows for ``crossings_list``.

    ``# type: ignore[misc]``: wx ships no stubs (pyproject.toml's
    ``ignore_missing_imports`` for ``wx.*``), so every wx member --
    ``DataViewIndexListModel`` included -- resolves to ``Any``, and
    mypy refuses to subclass ``Any``. Unavoidable for the first wx
    base class this codebase subclasses; nothing to fix here.

    The wx-facing half of the crossings feed; ``ui/feed_model.py``
    holds the column layout and the two decisions this class
    delegates to (``card_asset_key_or_none``, the flagged-row
    lookup), so those stay testable without ``wx``
    (``tests/unit/ui/test_feed_model.py``). This class has exactly
    one consumer, :class:`MainFrame`, which is why it lives here
    rather than in its own file (SIMPLECODE Rule 7).

    Rows are supplied once at construction -- :class:`MainFrame`
    builds a fresh model each time ``show_feed`` runs rather than
    mutating this one in place, which keeps the 30-row cap (R-32)
    trivial and avoids ``DataViewIndexListModel``'s row-count-change
    notifications entirely.
    """

    def __init__(self, rows: Sequence[FeedRow], card_images: CardImageList) -> None:
        """Wrap *rows*, newest first; render cards via *card_images*."""
        super().__init__(len(rows))
        self._rows = tuple(rows)
        self._card_images = card_images
        self._flagged = feed_model.flagged_row_indexes(self._rows)

    def GetColumnCount(self) -> int:
        """Return the feed's fixed seven columns."""
        return len(feed_model.COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:
        """Return each column's wx variant type.

        ``"wxBitmap"`` for the card column -- see :class:`MainFrame`'s
        ``_build_columns`` for why not the newer ``"wxBitmapBundle"``.
        """
        return "wxBitmap" if col == feed_model.COL_CARD else "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*."""
        feed_row = self._rows[row]
        if col == feed_model.COL_CARD:
            return self._card_bitmap(feed_row.card)
        return _TEXT_ACCESSORS[col](feed_row)

    def GetAttrByRow(self, row: int, col: int, attr: Any) -> bool:  # noqa: ANN401, ARG002
        """Bold the whole row when its crossing is flagged (R-34).

        *col* is unused: xrc-windows.md's code-side note bolds the
        entire flagged row, not one cell.
        """
        if row not in self._flagged:
            return False
        attr.SetBold(True)  # noqa: FBT003 -- wx API takes a positional bool
        return True

    def _card_bitmap(self, card: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the dealt card's bitmap, or a blank cell if held."""
        key = feed_model.card_asset_key_or_none(card)
        if key is None:
            return wx.NullBitmap
        return self._card_images.bitmap(key)


class MainFrame:
    """Code-side behaviour for ``main_frame`` (the console, 1a).

    Implements ``ConsoleView`` (module-skeletons.md's presenter
    contract). E4.4.1-E4.4.3 grew the Protocol with the four members
    the live presenter actually calls (``set_stop_enabled``,
    ``set_hide_times``, ``show_clock``, ``set_entry_locked``) -- the
    "add the member once the presenter calls it" precedent this
    class's own earlier docstring recorded for ``set_hide_times``.
    :meth:`wire_console` binds the lifecycle controls (start/arm/
    stop/undo) and the tick timer, mirroring :meth:`wire_entry`'s
    callback idiom; the app bootstrap (and the live-console harness)
    call it after construction.
    """

    def __init__(  # noqa: PLR0913 -- (frame, data_source, card_images) + the E4.4.2 stop-confirm resource
        self,
        frame: wx.Frame,
        *,
        data_source: DataSource,
        card_images: CardImageList | None = None,
        resource: Any | None = None,  # noqa: ANN401 -- wx ships no stubs
    ) -> None:
        """Decorate an already-loaded ``main_frame`` window.

        Args:
            frame: The ``wx.Frame`` ``harness.load_window`` (or the
                app bootstrap) already loaded from ``main.xrc``.
            data_source: The display-data seam (module-skeletons.md
                ``ui.presenters``). This view knows only the
                :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies (``EngineDataSource`` from
                E4.4.1, ``DemoDataSource`` on screens still demo).
            card_images: The card bitmaps for the feed's Card column;
                defaults to the packaged deck at 1x.
            resource: The loaded ``wx.xrc.XmlResource`` the Stop
                confirm dialog is loaded from (E4.4.2); ``None`` only
                in constructions that never open that dialog.
        """
        self.frame = frame
        self.data_source = data_source
        self.card_images = card_images if card_images is not None else default_card_images()
        self._stop_confirm_resource = resource

        self.crossings_list = self._find(ids.CROSSINGS_LIST, wx.dataview.DataViewCtrl)
        self.main_splitter = self._find(ids.MAIN_SPLITTER, wx.SplitterWindow)
        self.plate_input = self._find(ids.PLATE_INPUT, wx.TextCtrl)
        self.record_btn = self._find(ids.RECORD_BTN, wx.Button)
        self.last_crossing_lbl = self._find(ids.LAST_CROSSING_LBL, wx.StaticText)
        self.ride_status_lbl = self._find(ids.RIDE_STATUS_LBL, wx.StaticText)
        self.crossings_count_lbl = self._find(ids.CROSSINGS_COUNT_LBL, wx.StaticText)
        self.cards_count_lbl = self._find(ids.CARDS_COUNT_LBL, wx.StaticText)
        self.on_course_lbl = self._find(ids.ON_COURSE_LBL, wx.StaticText)
        self.shoe_lbl = self._find(ids.SHOE_LBL, wx.StaticText)

        # E4.4.1 lifecycle controls (start/arm/stop/undo + clock).
        self.start_btn = self._find(ids.START_BTN, wx.Button)
        self.arm_stop_chk = self._find(ids.ARM_STOP_CHK, wx.CheckBox)
        self.stop_btn = self._find(ids.STOP_BTN, wx.Button)
        self.undo_btn = self._find(ids.UNDO_BTN, wx.Button)
        self.clock_elapsed_lbl = self._find(ids.CLOCK_ELAPSED_LBL, wx.StaticText)
        self.clock_remaining_lbl = self._find(ids.CLOCK_REMAINING_LBL, wx.StaticText)
        # R-35: Stop is gated on arm_stop_chk -- disabled at rest even
        # if the XRC ever leaves it enabled (test_menu_state pins the
        # commands-side rule; this pins the actual control).
        self.stop_btn.Enable(False)  # noqa: FBT003 -- wx API takes a positional bool

        # LoadFrame does not honour main.xrc's <size> -- measured: the
        # frame comes back sized to the sizer's own computed minimum
        # (~429x373), not the canvas's 1100x700. SetMinSize alone only
        # stops *future* shrinking below the floor; SetSize is what
        # actually grows this window (and the splitter's client area)
        # to the canvas's documented size right now.
        self.frame.SetMinSize(wx.Size(*MIN_SIZE))
        self.frame.SetSize(wx.Size(*MIN_SIZE))

        self._next_infobar_slot = 1  # main.xrc's spacer placeholder sits at index 0
        self.resume_infobar = self._build_infobar(RESUME_INFOBAR)
        self.reopened_infobar = self._build_infobar(REOPENED_INFOBAR)
        self.finished_infobar = self._build_infobar(FINISHED_INFOBAR)

        self._hideable_columns = self._build_columns()
        self._crossings_model: CrossingsFeedModel | None = None

        # Reflow now that the size and every sizer item are final, so
        # the splitter has its real client area before a sash position
        # is read or restored.
        self.frame.Layout()

        self.main_splitter.Bind(wx.EVT_SPLITTER_SASH_POS_CHANGED, self._on_sash_changed)
        self._restore_sash_position()

        self.show_feed(self.data_source.feed_rows())
        self.show_counters(self.data_source.counters())

    # ------------------------------------------------------- lookups

    def _find(self, name: str, expected_type: type = wx.Window) -> Any:  # noqa: ANN401
        """Resolve one of this frame's own child controls by name.

        See :func:`find_control`'s docstring (``ui.views._support``)
        for the full measured reasoning this mirrors: an explicit
        ``self.frame`` parent scopes the lookup, and the retry loop
        settles the address-reuse hazard this wx build exhibits
        under sustained window churn.

        Raises:
            LookupError: If *name* does not resolve to an
                *expected_type* instance inside this frame, even
                after settling.
        """
        return find_control(self.frame, name, expected_type)

    # ------------------------------------------------------- InfoBars

    def _build_infobar(self, name: str) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build one code-side InfoBar and insert it after the spacer.

        ``main.xrc``'s spacer placeholder sits at sizer index 0; each
        InfoBar is inserted right after it (and after any InfoBar
        already inserted), so the three stack in call order. A fresh
        ``wx.InfoBar`` starts hidden (measured) -- nothing further is
        needed for R-73's "hidden by default".

        Measured (wxPython 4.3.1 / wxWidgets 3.3.3, macOS, a throwaway
        probe script per this repo's convention, first reproduced
        wiring ``rider_editor_dlg``'s ``roster_infobar``, E3.2):
        ``Dismiss()``/``ShowMessage()`` on a ``wx.InfoBar`` with its
        default slide effect never returns, shown or not -- disabling
        both effects here is what keeps a future ``ShowMessage()``/
        ``Dismiss()`` call on any of these three safe.
        """
        bar = wx.InfoBar(self.frame)
        bar.SetName(name)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        self.frame.GetSizer().Insert(self._next_infobar_slot, bar, 0, wx.EXPAND)
        self._next_infobar_slot += 1
        return bar

    # ------------------------------------------------------- columns

    def _build_columns(self) -> tuple[Any, ...]:
        """Append the feed's seven columns in canvas order.

        Returns:
            The hide-times-affected columns (Lap time, Total), in
            column order, for :meth:`set_hide_times` to toggle.

        The Card column uses an explicit
        ``DataViewBitmapRenderer("wxBitmap")`` rather than
        ``AppendBitmapColumn``'s default: this wx build (4.3.1 /
        wxWidgets 3.3.3, probed with a throwaway script per this
        repo's convention) registers that default renderer against
        ``"wxBitmapBundle"``, which silently drops a plain
        ``wx.Bitmap`` value. The explicit ``"wxBitmap"`` renderer
        accepts ``CardImageList.bitmap()``'s values unchanged.
        """
        hideable = []
        for col, label in enumerate(feed_model.COLUMN_LABELS):
            if col == feed_model.COL_CARD:
                renderer = wx.dataview.DataViewBitmapRenderer("wxBitmap")
                column = wx.dataview.DataViewColumn(label, renderer, col)
                self.crossings_list.AppendColumn(column)
            else:
                column = self.crossings_list.AppendTextColumn(label, col)
            if col in feed_model.TIME_COLUMNS:
                hideable.append(column)
        return tuple(hideable)

    def set_hide_times(self, *, hide: bool) -> None:
        """Toggle the Lap time/Total columns per R-37.

        The clock (``clock_elapsed_lbl``/``clock_remaining_lbl``) is
        untouched -- R-37 keeps it visible regardless of this setting.
        """
        for column in self._hideable_columns:
            column.SetHidden(hide)

    # -------------------------------------------------------- splitter

    def _restore_sash_position(self) -> None:
        """Apply any sash position already saved this process."""
        if _persisted_main_splitter_sash is not None:
            self.main_splitter.SetSashPosition(_persisted_main_splitter_sash)

    def persist_layout(self) -> None:
        """Save ``main_splitter``'s current sash position.

        Bound to the sash-changed event for real dragging; wx only
        fires that event from genuine user drag input, never from a
        programmatic ``SetSashPosition`` (measured), so tests call
        this directly instead (CODINGSTANDARDS-UX-DESKTOP.md section
        6: sash positions must persist across restarts).
        """
        global _persisted_main_splitter_sash  # noqa: PLW0603 -- see module docstring
        _persisted_main_splitter_sash = self.main_splitter.GetSashPosition()

    def _on_sash_changed(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Persist a real, user-driven sash drag."""
        event.Skip()
        self.persist_layout()

    # ----------------------------------------------------- ConsoleView

    def show_feed(self, rows: list[FeedRow]) -> None:
        """Render the crossings feed, newest first (ConsoleView)."""
        self._crossings_model = CrossingsFeedModel(rows, self.card_images)
        self.crossings_list.AssociateModel(self._crossings_model)

    def show_counters(self, c: Counters) -> None:
        """Render the four counter chips (ConsoleView)."""
        self.crossings_count_lbl.SetLabel(_format_count(c.crossings))
        self.cards_count_lbl.SetLabel(_format_count(c.cards_dealt))
        self.on_course_lbl.SetLabel(_format_count(c.on_course))
        self.shoe_lbl.SetLabel(f"{c.shoe_remaining}/{c.shoe_total}")

    def flash_crossing(self, r: FeedRow) -> None:
        """Highlight the just-recorded crossing (ConsoleView)."""
        self.last_crossing_lbl.SetLabel(
            f"✓ {r.plate} · {r.entry} · Lap {r.lap} · {r.lap_time} · dealt {r.card}"
        )

    def set_state(self, status: RideStatus) -> None:
        """Reflect the ride's lifecycle state (ConsoleView).

        The status label and record-crossing row enablement (A4:
        ``record_btn`` tracks ``plate_input``, both live only in
        RUNNING), and the REOPENED corrections banner: REOPENED is a
        corrections-only state (spec §3, R-36), so the console shows
        ``reopened_infobar`` to say entry is off and corrections are
        on (E5.2.2), and dismisses it for every other status.
        """
        self.ride_status_lbl.SetLabel(status.value.upper())
        running = status == RideStatus.RUNNING
        self.plate_input.Enable(running)
        self.record_btn.Enable(running)
        if status is RideStatus.REOPENED:
            self.reopened_infobar.ShowMessage(REOPENED_BANNER, wx.ICON_INFORMATION)
        else:
            self.reopened_infobar.Dismiss()

    def focus_entry(self) -> None:
        """Return focus to the plate entry field (ConsoleView)."""
        self.plate_input.SetFocus()

    def show_notice(self, text: str) -> None:
        """Post *text* to the status bar's first field (ConsoleView)."""
        self.frame.SetStatusText(text, 0)

    def clear_entry(self) -> None:
        """Empty the plate entry field (ConsoleView).

        ``ChangeValue``, not ``SetValue``: wx's own documented
        contract is that ``ChangeValue`` does not fire ``EVT_TEXT``,
        so clearing the field after a submit cannot loop back into
        any future EVT_TEXT-driven validation.
        """
        self.plate_input.ChangeValue("")

    def wire_entry(self, on_submit: Callable[[str], None]) -> None:
        """Bind Enter and Record to *on_submit* with the field's text.

        Binds ``EVT_TEXT_ENTER`` (``plate_input`` carries
        ``wxTE_PROCESS_ENTER``) and ``EVT_BUTTON`` (``record_btn``)
        to the same call. Neither handler calls ``event.Skip()``,
        and ``record_btn`` never gets ``SetDefault()``: with
        ``wxTE_PROCESS_ENTER``, ``Skip()`` would fall through to
        wx's own default-button dispatch and fire a second submit
        for the one Enter keypress.

        The callback is stored as :attr:`_on_submit` and every
        handler routes through it, so :meth:`set_presenter` can swap
        the console onto a new ride without rebinding (E5.4.1's
        library Open).
        """
        self._on_submit = on_submit

        def _submit(_event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
            self._on_submit(self.plate_input.GetValue())

        self.plate_input.Bind(wx.EVT_TEXT_ENTER, _submit)
        self.record_btn.Bind(wx.EVT_BUTTON, _submit)

    def play(self, cue: Cue) -> None:
        """Play the audio cue for the given event (ConsoleView, R-31).

        Delegates to ``ui.sound``'s default player (E4.4.3); the
        player is wx-lazy and never blocks the entry field (spec §10).
        """
        sound.play(cue)

    def set_stop_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the Stop button (ConsoleView, R-35).

        Disarming also unticks ``arm_stop_chk`` so the checkbox
        visibly reflects the presenter's auto-clear (after use or
        timeout); ``SetValue`` fires no ``EVT_CHECKBOX`` (measured
        harness convention), so this cannot loop back into the
        presenter.
        """
        self.stop_btn.Enable(enabled)
        if not enabled:
            self.arm_stop_chk.SetValue(False)  # noqa: FBT003 -- wx API takes a positional bool

    def show_clock(self, elapsed: str, remaining: str) -> None:
        """Render the ride clock's labels (ConsoleView).

        ``clock_remaining_lbl`` carries no canvas label; the elapsed
        label's default ``0:00:00`` comes from main.xrc.
        """
        self.clock_elapsed_lbl.SetLabel(elapsed)
        self.clock_remaining_lbl.SetLabel(remaining)

    def set_entry_locked(self, *, locked: bool) -> None:
        """Lock or unlock the plate entry row (ConsoleView, R-35).

        Stop is a guard, not a state: after ``engine.stop()`` the ride
        still reads RUNNING, so ``set_state`` would re-enable the
        entry row -- this is the "only confirming locks the entry
        field" channel on top of it.
        """
        self.plate_input.Enable(not locked)
        self.record_btn.Enable(not locked)

    def wire_console(self, presenter: ConsolePresenter) -> None:
        """Bind the lifecycle controls and tick timer to *presenter*.

        Mirrors :meth:`wire_entry`'s callback idiom: Start Ride,
        the Arm checkbox, Stop Ride (through the confirm dialog,
        :meth:`_on_stop_clicked`) and Undo each forward to the
        presenter, and a 1 s ``wx.Timer`` drives ``presenter.tick()``
        (feed/counters/clock refresh + R-35's 10 s arm auto-clear).

        The presenter is stored as :attr:`_presenter` and every
        handler routes through it, so :meth:`set_presenter` can swap
        the console onto a store-loaded ride without rebinding the
        controls or the timer (E5.4.1's library Open).
        """
        self._presenter = presenter
        self.start_btn.Bind(wx.EVT_BUTTON, lambda _event: self._presenter.on_start())
        self.arm_stop_chk.Bind(
            wx.EVT_CHECKBOX,
            lambda _event: self._presenter.on_arm_stop(armed=self.arm_stop_chk.GetValue()),
        )
        self.stop_btn.Bind(wx.EVT_BUTTON, lambda _event: self._on_stop_clicked())
        self.undo_btn.Bind(wx.EVT_BUTTON, lambda _event: self._presenter.on_undo())
        self._tick_timer = wx.Timer(self.frame)
        self.frame.Bind(wx.EVT_TIMER, lambda _event: self._presenter.tick(), self._tick_timer)
        self._tick_timer.Start(_TICK_MS)

    def set_presenter(self, presenter: ConsolePresenter) -> None:
        """Swap the console's bound presenter (E5.4.1 library Open).

        :meth:`wire_entry`/:meth:`wire_console` route every handler
        through :attr:`_on_submit`/:attr:`_presenter`, so replacing
        those two references rewires the whole console -- plate entry,
        start/arm/stop/undo, the tick timer -- without rebinding any
        control or starting a second timer. The caller then re-renders
        state/feed/counters from the new presenter's source.
        """
        self._on_submit = presenter.on_plate_entered
        self._presenter = presenter

    def _on_stop_clicked(self) -> None:
        """Handle Stop Ride: R-35's confirm, then ``on_stop_confirmed``.

        Loads ``stop_confirm_dlg`` from the constructor's resource and
        shows it through ``dialogs.run_dialog`` -- the one entry point
        every dialog in this codebase shows through -- so the default
        + focused Cancel is honoured (test_dialog_behavior pins it on
        the raw dialog) and only ``wxID_OK`` ("Stop ride") confirms.
        A construction with no resource (a screen that never opens the
        dialog) posts a notice rather than silently stopping.

        # logic-coverage-exempt: T-3 -- the two ``is None`` guards are
        # defensive for constructions that never open the dialog (the
        # shared read-only fixtures); every live construction supplies
        # the resource and the OK/Cancel arms are driven functionally.
        """
        from rivercrossing.ui.views import dialogs  # noqa: PLC0415 -- deferred, see app.py

        if self._stop_confirm_resource is None:
            self.show_notice("Stop confirm unavailable")
            return
        dialog = self._stop_confirm_resource.LoadDialog(None, ids.STOP_CONFIRM_DLG)
        if dialog is None:
            self.show_notice("Stop confirm unavailable")
            return
        try:
            result = dialogs.run_dialog(dialog, opener=self.stop_btn)
        finally:
            if not dialog.IsBeingDeleted():
                dialog.Destroy()
        if result == wx.ID_OK:
            self._presenter.on_stop_confirmed()


def _format_count(value: int) -> str:
    """Render *value* with a space as the thousands separator.

    Matches the canvas exactly: 1124 -> "1 124", 42 -> "42".
    """
    return f"{value:,}".replace(",", " ")
