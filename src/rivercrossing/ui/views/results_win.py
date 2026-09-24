# SPDX-License-Identifier: GPL-3.0-only
"""``ResultsWindow``: results_dlg (1f), standings (E1.5.2/E6.4.1).

xrc-windows.md section D's code-side footnote puts the standings
lists' columns and rows in code -- ``results.xrc``'s own header
explains why (``wxDataViewListCtrl`` would overwrite the frozen
name). This module is that binding.

The canvas's "Best 5" cell is plain text carrying suit glyphs ("K♠
K♣ K♦ JK★ 9♥"), confirmed against ``design/docs-html``'s own table
markup (a literal ``<td>`` string, not five drawn bitmaps) -- unlike
``main_frame.py``'s Card column, the standings lists need no
``DataViewBitmapRenderer``.

The two card columns ("Best 5" and Draw) colour **each red card on its
own**: a ``DataViewItemAttr`` carries one text colour per CELL, and the
five-card "Best 5" cell holds mixed suits, so a whole-cell colour would
paint its ♠/♣ faces red -- the wrong suit, not merely a redundant cue.
Both columns therefore render wx markup, one
``<span color="...">`` per ♥/♦ card (``ui.card_text.card_markup``),
through ``ui.views._support.append_markup_column``; the ♠/♣ cards come
through bare, so they take the list's own adaptive foreground and stay
visible on a dark background. The red itself follows the appearance
(``theme.card_red()``), resolved when a model is built. The glyph still
spells each suit, so the colour is decoration, never the sole channel
(CODINGSTANDARDS-UX-DESKTOP.md §7).

:func:`format_best5` and :func:`format_draw` are the pure cell
formatters both columns use, and :func:`format_place` the E6.4.1 ⚠
badge formatter (a draw_required row renders ``"⚠ 2"`` in its Place
cell -- this task's own reading of the footnote's "⚠ badge column": the
canvas's own columns and their order are frozen, so a badge column
would shift every frozen index; the badge instead leads the Place cell,
where a scorer's eye lands first, and Phase 5's activation alert
explains it).

Phase 3 split the standings by entry kind. A MIXED ride renders its
Teams and Solo sections on the two ``results_notebook`` pages
(``teams_standings_list``/``solo_standings_list``); a SOLO-only ride
hides the notebook and shows the one standalone ``standings_list``.
The view switches on the ``entry_mode`` the app threads in, so the
kind split needs no extra column and no merged header row.

E7.3.2's stale-export flag is the one live banner: ``set_stale``
shows/hides the code-side ``stale_infobar`` (xrc-windows.md's
code-side footnote; XRC cannot author a ``wxInfoBar`` -- results.xrc's
own header). The window's one presenter (``self.presenter``, built
here like ``RideSetup`` builds its own) ranks the rows and drives that
banner.

G6 reworked the dialog's surface: the Total and Best lap columns are
gone (the standings table is Place, Plate, Entry, Laps, Best 5, Hand,
each pinned to its own width), the five publish checkboxes left the
dialog for the Results menu (``app._RESULTS_PUBLISH_MENU_IDS``, their
defaults now persisted in ``AppSettings.publish_*``), and the R-63
gate between show-times and the Fastest-time board moved with them --
the dialog no longer owns either.

R-14 added the Draw column beside Hand: the card the row's entry drew
for the venue's high-card tie-break (``StandingsRow.tiebreak_card``),
blank on every other row. It is deliberately not the ⚠ flag's own
column -- a drawn tie is *resolved*, so the badge and its note are for
the residual tie a configured order could not separate.

Phase 5 adds two display facts the canvas cannot carry:

- the ⚠ badge's explanation (Part 1). A ``wxDataViewCtrl`` has no
  per-row hover tooltip, and hover is unreachable by keyboard anyway
  (CODINGSTANDARDS-UX-DESKTOP §7), so the badge is explained on the
  activation gesture instead: double-clicking (or pressing Enter on) a
  draw row opens an OK-only alert carrying the row's own
  ``tie_note``. ``standings``' draw flag and note are threaded through
  ``StandingsRow`` for exactly this.
- the Plate column's absence on the Team list under ``RIDER_POOLED``
  (Part 2): a pooled team's plate is *derived* from its members, so
  the column repeats a member's plate while the Entry column names
  the team. Under ``TEAM_RELAY`` the plate is the entry's identity and
  stays.
- the Hand column's width (G6): the solo and standalone lists pin it
  at 210, and the Team list at 260 when the Plate column it drops has
  freed 50 more.

E6.4.3 adds the self-test note: a ride the operator finished over a
failed evaluator self-test reads "Self-test unverified" in the same
code-side banner the stale-export flag uses (the dialog's one note
seam), so a published-standing window never hides the fact.

The UI-removals batch retired W11's export-button row: the four
buttons left ``results.xrc`` for the Results menu's own ``mi_export_*``
rows, so this window carries no export surface at all and the
``on_export(target)`` seam (with its ``_bind_export_buttons`` wiring)
went with them.

``_find`` is now inherited from ``ui.views._support.DialogFindMixin``
-- see that module's docstring for why it used to be duplicated here.
"""

from typing import TYPE_CHECKING, Any

import wx
import wx.dataview

from rivercrossing.cards import Card, draw_key
from rivercrossing.ride import DEFAULT_TIEBREAK_ORDER
from rivercrossing.roster import EntryMode, PlateModel
from rivercrossing.standings import DRAW_TIE_NOTE
from rivercrossing.ui import ids, theme
from rivercrossing.ui.card_text import (
    JOKER_CODE,
    JOKER_DISPLAY,
    SUIT_RED,
    card_markup,
    cards_markup,
    format_card,
)
from rivercrossing.ui.presenters.results import ResultsPresenter
from rivercrossing.ui.std_dialogs import show_info
from rivercrossing.ui.views._support import (
    DialogFindMixin,
    _ordering,
    append_markup_column,
    associate_model,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from rivercrossing.ui.presenters.data_source import DataSource, StandingsRow

__all__ = [
    "COLUMN_LABELS",
    "COLUMN_WIDTHS",
    "COL_BEST5",
    "COL_DRAW",
    "COL_ENTRY",
    "COL_HAND",
    "COL_LAPS",
    "COL_PLACE",
    "COL_PLATE",
    "DRAW_EXPLANATION",
    "DRAW_INFO_TITLE",
    "DRAW_WIDTH",
    "HAND_WIDTH",
    "JOKER_CODE",
    "JOKER_DISPLAY",
    "MARKUP_COLUMNS",
    "MIN_SIZE",
    "SELF_TEST_NOTE",
    "STALE_INFOBAR",
    "STALE_NOTE",
    "STANDINGS_COLUMN_FLAGS",
    "STANDINGS_HEADER_HEIGHT",
    "STANDINGS_LIST_MIN_HEIGHT",
    "STANDINGS_MIN_ROWS",
    "STANDINGS_ROW_HEIGHT",
    "TEAM_HAND_WIDTH",
    "TIE_BADGE",
    "ResultsWindow",
    "StandingsListModel",
    "draw_info_message",
    "format_best5",
    "format_card",
    "format_draw",
    "format_laps",
    "format_place",
]

COL_PLACE = 0
COL_PLATE = 1
COL_ENTRY = 2
COL_LAPS = 3
COL_BEST5 = 4
COL_HAND = 5
# R-14's Draw column: appended last so G6's six indexes (and the Team
# list's Hand-width rule, which reads COL_PLATE) keep their pinned
# meanings.
COL_DRAW = 6

# The two columns that render markup: each red card in the cell takes
# its own suit red (``ui.card_text``). Drawn from the column indexes so
# the set cannot drift from the accessors below.
MARKUP_COLUMNS = frozenset({COL_BEST5, COL_DRAW})

# xrc-windows.md D's column order after G6, which dropped the two
# ride-clock columns (Total and Best lap): the scorer reads place,
# plate, entry, laps, cards, hand -- and, since R-14, the card each
# entry drew for the venue's tie-break.
COLUMN_LABELS: tuple[str, ...] = (
    "Place",
    "Plate",
    "Entry",
    "Laps",
    "Best 5",
    "Hand",
    "Draw",
)

# G6's pinned widths: the Hand column has two variants (below), every
# other column is the same on every list. The seven total 750px, which
# is what the dialog's 800px floor is built from.
HAND_WIDTH = 210
# Wide enough for a two-character rank plus its suit glyph ("10♦"), or
# the joker's "JK★".
DRAW_WIDTH = 60
COLUMN_WIDTHS: tuple[int, ...] = (60, 50, 160, 50, 160, HAND_WIDTH, DRAW_WIDTH)

# The Team list's Hand width under RIDER_POOLED: its Plate column is
# hidden, so Hand takes the width it frees (HAND_WIDTH + 50).
TEAM_HAND_WIDTH = 260


# E6.4.1: the R-43 "draw required" badge (xrc-windows.md D's code-side
# footnote), rendered as a leading glyph in the Place cell (module
# docstring).
TIE_BADGE = "⚠"

# The ⚠ badge's own explanation (Phase 5, Part 1). A wxDataViewCtrl has
# no per-row hover tooltip and hover is keyboard-unreachable anyway
# (CODINGSTANDARDS-UX-DESKTOP §7), so the badge is explained on the
# activation gesture -- double-click or Enter -- in an OK-only alert:
# the row's own tie note (standings' DRAW_TIE_NOTE), then this one plain
# sentence saying what a residual tie means. R-14 added the Draw column,
# so a flagged row is one the *configured* order could not separate --
# never a tie the venue's draw would still settle.
DRAW_INFO_TITLE = "Draw required"
DRAW_EXPLANATION = (
    "Identical best hands were not separated by the configured tie-break order — "
    "the venue decides."
)

# D16: XRC has no window-level minsize (results.xrc's own header notes
# this and defers to code). Width floor measured on wxPython 4.3.1 /
# wxWidgets 3.3.3: the solo tab's seven pinned columns total 750px
# (G6's COLUMN_WIDTHS plus the R-14 Draw pin), plus the list's
# scrollbar (16) and its notebook and sizer borders (~34) = 800, so the
# Solo tab displays fully at the min width. Height is Fit()'s own
# measurement of the real sizer content -- see this task's own report
# for how it was measured.
MIN_SIZE = (800, 442)

# D16's row floor: the three standings lists hold ten rows -- the
# scorer's own working set -- rather than Fit()'s measurement of
# whatever the loaded ride happened to carry. The 17px row and 28px
# header are wxDataViewCtrl's measured metrics on wxPython 4.3.1 /
# wxWidgets 3.3.3, so the floor is 198px.
STANDINGS_MIN_ROWS = 10
STANDINGS_ROW_HEIGHT = 17
STANDINGS_HEADER_HEIGHT = 28
STANDINGS_LIST_MIN_HEIGHT = STANDINGS_HEADER_HEIGHT + STANDINGS_MIN_ROWS * STANDINGS_ROW_HEIGHT

# The stale-export InfoBar's frozen name (xrc-windows.md D / spec.md
# §15b). XRC cannot author a wxInfoBar at all (results.xrc's own
# header), so this name never appears in ui/ids.py -- the bar is built
# code-side and named with SetName(), the pattern rider_editor.py's
# ROSTER_INFOBAR follows.
STALE_INFOBAR = "stale_infobar"

# The two notes that bar carries. E7.3.2's stale-export warning was a
# literal inside ``set_stale``; E6.4.3's self-test note joins it, and
# both live here so the tests pin the exact copy the operator reads.
STALE_NOTE = "Results are stale — re-export to refresh"
SELF_TEST_NOTE = "Self-test unverified — the ride finished over a failed evaluator self-test"

# AppendTextColumn's own default flags include
# wxDATAVIEW_COL_RESIZABLE, but an explicit flags= argument *replaces*
# the default rather than OR-ing into it -- macOS then sets the column
# NSTableColumnNoResizing -- so both bits must be spelled out
# (mirrors rider_editor.py's RIDERS_LIST_COLUMN_FLAGS).
STANDINGS_COLUMN_FLAGS = wx.dataview.DATAVIEW_COL_SORTABLE | wx.dataview.DATAVIEW_COL_RESIZABLE


# E6.4.2's export actions now live only on the Results menu rows
# (``mi_export_html`` and friends): the dialog's four export buttons
# retired with the UI-removals batch, so one surface owns every export.


def format_best5(cards: Sequence[str], red: str = SUIT_RED) -> str:
    """Return the "Best 5" cell value for *cards*.

    Space-joined, canvas exact, each ♥/♦ card in *red*:
    ``("KS", "KC", "KD", "JK", "9H")`` ->
    ``'K♠ K♣ <span color="#c0392b">K♦</span> JK★ …'``. The column is
    built with the markup renderer (:data:`MARKUP_COLUMNS`), so this
    markup *is* what the scorer reads; the glyph beside each colour
    still spells its suit, and the ♠/♣ cards come through bare so the
    list paints them in its own foreground. An empty hand is the empty
    cell.

    The column's **sort key** is not this string: sorting
    ``<span color=…>`` would order the list by colour before rank
    (:func:`_best5_sort_key`).
    """
    return cards_markup(cards, red)


def _best5_sort_key(standing: StandingsRow) -> str:
    """Return the Best 5 sort key: the canvas text, never markup.

    Keyed on :func:`format_best5`'s markup, a red deuce would sort after
    a bare ace (the span text ``#c0392b…`` outranks ``A♠``); the key is
    therefore the glyph text the cell *displays*, which is what the
    column ordered by before it carried colour at all.
    """
    return " ".join(format_card(card) for card in standing.best5)


def format_place(standing: StandingsRow) -> str:
    """Return the Place cell text for *standing*.

    A ``draw_required`` row (R-43's unresolved hand tie) carries the
    ⚠ badge ahead of its place -- ``"⚠ 2"`` -- the E6.4.1 reading of
    the footnote's "⚠ badge column" (module docstring); every other
    row is the bare place number.

    A DNS row (Phase 7) has no place at all -- it is listed at the
    bottom of its section -- so its cell is blank; the ``dns`` gate
    must come first, since the row's ``place`` is only the placeholder
    0 that no renderer reads.
    """
    if standing.dns:
        return ""
    if standing.draw_required:
        return f"{TIE_BADGE} {standing.place}"
    return str(standing.place)


def format_laps(standing: StandingsRow) -> str:
    """Return the Laps cell text for *standing* (Phase 7).

    A DNS row names its missing start ("DNS") rather than the number 0
    a live board shows for an entry that has not crossed yet; every
    other row renders its lap count.
    """
    if standing.dns:
        return "DNS"
    return str(standing.laps)


def format_draw(standing: StandingsRow, red: str = SUIT_RED) -> str:
    """Return the Draw cell value for *standing*, red cards in *red*.

    The card the entry drew for the venue's high-card tie-break, in the
    canvas's own card text and the appearance's own suit red
    (``card_markup``: ``'<span color="#c0392b">10♦</span>'``). A row
    whose entry drew nothing -- no hand tie, or a board read before the
    finish's draw -- renders an empty cell. The column carries the
    markup renderer (:data:`MARKUP_COLUMNS`), and the sort key is the
    parsed card, never this string (:func:`_draw_sort_key`).

    The blank guard answers before ``card_markup``: the empty code
    would reach ``format_card``, which raises ``IndexError`` on it.
    """
    if not standing.tiebreak_card:
        return ""
    return card_markup(standing.tiebreak_card, red)


def draw_info_message(standing: StandingsRow) -> str:
    """Return the ⚠ explanation alert's body for *standing*.

    The row's own tie note leads (``standings.DRAW_TIE_NOTE``, "draw
    required"), then :data:`DRAW_EXPLANATION`'s plain sentence. A row
    whose note is unset -- a hand-built row, or an export-era stub --
    still reads "draw required" rather than the word "None".
    """
    note = standing.tie_note or DRAW_TIE_NOTE
    return f"{note}\n\n{DRAW_EXPLANATION}"


_TEXT_ACCESSORS: tuple[Callable[[StandingsRow], str], ...] = (
    format_place,
    lambda standing: standing.plate,
    lambda standing: standing.entry,
    format_laps,
    lambda standing: format_best5(standing.best5),
    lambda standing: standing.hand,
    format_draw,
)

# The blank Draw cell's key: below every card ``cards.draw_key`` can
# return (its floor is the two of clubs' 8), so a row that drew nothing
# is "no card at all" -- an ascending sort lists the drawn cards last,
# and the downward arrow reads the highest card first.
_NO_DRAW_SORT_KEY = -1


def _draw_sort_key(standing: StandingsRow) -> int:
    """Return the Draw column's rank-then-suit key for *standing*.

    The stored code parses back to its card and keys through
    ``cards.draw_key`` -- the finish's own order, rank major and suit
    minor (clubs < diamonds < hearts < spades), which is also how
    ``standings`` orders a drawn tie group -- so the column orders by
    the card, never by the code's text (which reads "10D" before "2C").
    A row that drew nothing keys below every card
    (:data:`_NO_DRAW_SORT_KEY`). The parse and the key both refuse what
    the draw's jokerless deck cannot deal -- a joker, a malformed code
    -- the same loud refusal ``standings``' key makes.
    """
    if not standing.tiebreak_card:
        return _NO_DRAW_SORT_KEY
    return draw_key(Card.parse(standing.tiebreak_card))


# The native header sort's per-column key, in ``COLUMN_LABELS`` order:
# Place and Laps are ints, the strings sort as text -- except Best 5,
# which keys on the plain canvas text (``_best5_sort_key``: never the
# markup, or the list would order by colour before rank), and Draw,
# which keys on the parsed card (:func:`_draw_sort_key`).
# Place and Laps both lead with the ``dns`` flag, so a DNS row sorts
# after every placed row on either column instead of onto its
# placeholder place 0 or its 0 laps.
_STANDINGS_SORT_KEYS: tuple[Callable[[StandingsRow], Any], ...] = (
    lambda standing: (standing.dns, standing.place),
    lambda standing: standing.plate,
    lambda standing: standing.entry,
    lambda standing: (standing.dns, standing.laps),
    _best5_sort_key,
    lambda standing: standing.hand,
    _draw_sort_key,
)


class StandingsListModel(wx.dataview.DataViewIndexListModel):  # type: ignore[misc]
    """Read-only model over ``StandingsRow`` rows for a standings list.

    ``# type: ignore[misc]``: wx ships no stubs, so mypy refuses to
    subclass ``Any`` -- the same unavoidable annotation
    ``CrossingsFeedModel`` carries in ``views/main_frame.py``.
    """

    def __init__(self, rows: Sequence[StandingsRow], red: str = SUIT_RED) -> None:
        """Wrap *rows*, colouring red cards in *red*."""
        super().__init__(len(rows))
        self._rows = tuple(rows)
        self._red = red

    def GetColumnCount(self) -> int:
        """Return the standings' fixed column count (COLUMN_LABELS)."""
        return len(COLUMN_LABELS)

    def GetColumnType(self, col: int) -> str:  # noqa: ARG002 -- every column is text here
        """Return "string" -- every column here is text."""
        return "string"

    def GetValueByRow(self, row: int, col: int) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Return the cell value at *row*/*col*.

        The two card columns' values are markup -- one coloured span per
        card (:data:`MARKUP_COLUMNS`) -- because a per-cell attribute
        cannot colour a five-card hand's mixed suits. Both render
        through this model's own ``red`` rather than the default-red
        accessors in :data:`_TEXT_ACCESSORS`; the other five columns
        stay plain text.
        """
        standing = self._rows[row]
        if col == COL_BEST5:
            return format_best5(standing.best5, self._red)
        if col == COL_DRAW:
            return format_draw(standing, self._red)
        return _TEXT_ACCESSORS[col](standing)

    def standing_at(self, row: int) -> StandingsRow | None:
        """Return the row at *row*, or None when *row* names none.

        The activation handler's own lookup: ``GetRow`` answers a
        not-found item with wxNOT_FOUND (0xFFFFFFFF, measured on this
        build), so the range guard is what turns every unresolvable item
        into "no row" rather than a crash.
        """
        if 0 <= row < len(self._rows):
            return self._rows[row]
        return None

    def Compare(  # noqa: PLR0913, PLR0917 -- wx's own four-argument callback shape
        self,
        item1: Any,  # noqa: ANN401 -- wx ships no stubs
        item2: Any,  # noqa: ANN401 -- wx ships no stubs
        col: int,
        ascending: bool,  # noqa: FBT001 -- wx's own callback argument
    ) -> int:
        """Return the Ordering of *item1* versus *item2* on *col*.

        The native header arrows' answer: the control hands this two
        items and the model column, and the comparison runs on the
        rows those items index (``DataViewIndexListModel.GetRow``),
        keyed per column by :data:`_STANDINGS_SORT_KEYS`. Place and
        Laps compare as integers, so a displayed ``"10"`` never sorts
        before ``"9"``.

        Equal keys fall back to the row's own position, which is
        unique: wx's control-side sort is not stable, so without the
        tie-break two rows showing the same cell could reorder freely
        between sorts. The tie-break is deliberately *not* negated for
        the downward arrow, so equal-key rows keep their original
        order in both directions (mirrors
        ``RiderRowListModel.Compare`` in ``ui.views._support``).
        *ascending* is the arrow's own direction.
        """
        first_row = self.GetRow(item1)
        second_row = self.GetRow(item2)
        sort_key = _STANDINGS_SORT_KEYS[col]
        result = _ordering(sort_key(self._rows[first_row]), sort_key(self._rows[second_row]))
        if result == 0:
            return _ordering(first_row, second_row)
        return result if ascending else -result


class ResultsWindow(DialogFindMixin):  # _find: ui.views._support
    """Code-side behaviour for ``results_dlg`` (1f).

    Implements the :class:`~rivercrossing.ui.presenters.results.
    ResultsView` contract in full (E6.4.1): ``show_standings`` (the
    MIXED notebook or the SOLO standalone list, per ``entry_mode``),
    ``set_stale`` (the code-side stale_export banner E7.3.2 triggers
    after post-export corrections), and the standings lists' own
    column sets (G6). The one live presenter is built here, the same
    ``RideSetup`` precedent.
    """

    # (dialog, data_source) + the tie-break order, export-watermark,
    # entry-mode, plate-model and DNS-display seams
    def __init__(  # noqa: PLR0913
        self,
        dialog: wx.Dialog,
        *,
        data_source: DataSource,
        tiebreak_order: tuple[str, str, str] = DEFAULT_TIEBREAK_ORDER,
        export_watermark: int | None = None,
        entry_mode: EntryMode = EntryMode.SOLO,
        plate_model: PlateModel = PlateModel.RIDER_POOLED,
        show_dns_riders: bool = True,
    ) -> None:
        """Decorate an already-loaded ``results_dlg`` window.

        Args:
            dialog: The ``wx.Dialog`` the caller already loaded
                from ``results.xrc``.
            data_source: The display-data seam. This view knows only
                the :class:`~rivercrossing.ui.presenters.data_source.
                DataSource` Protocol -- the caller wires in whichever
                implementation applies.
            tiebreak_order: The ride's stored tie-break spellings, in
                priority order (``RideConfig.tiebreak_order``); the
                presenter ranks the standings with them.
            export_watermark: The engine event count at the last
                export (E7.3.2); the presenter evaluates the stale
                banner against it on the first render. ``None`` when
                nothing was exported.
            entry_mode: The ride's entry mode (``RideConfig.
                entry_mode``). MIXED shows the two-page notebook; SOLO
                shows the standalone ``standings_list``.
            plate_model: The ride's plate policy (``RideConfig.
                plate_model``). Under ``RIDER_POOLED`` the Team list
                drops its Plate column: a pooled team's plate is derived
                from its members' plates, so the column repeats one
                member's plate and the Entry column is the team's
                identity. Under ``TEAM_RELAY`` the entry's own plate is
                the identity, so the column stays.
            show_dns_riders: The Results menu's "Show DNS Riders"
                (``AppSettings.show_dns_riders``), forwarded to the
                presenter.
        """
        self.dialog = dialog
        self.data_source = data_source
        self.entry_mode = entry_mode
        self.plate_model = plate_model

        self.standings_list = self._find(ids.STANDINGS_LIST, wx.dataview.DataViewCtrl)
        self.teams_standings_list = self._find(ids.TEAMS_STANDINGS_LIST, wx.dataview.DataViewCtrl)
        self.solo_standings_list = self._find(ids.SOLO_STANDINGS_LIST, wx.dataview.DataViewCtrl)
        self.results_notebook = self._find(ids.RESULTS_NOTEBOOK, wx.Notebook)

        self._build_columns()
        self._model: StandingsListModel | None = None
        self._teams_model: StandingsListModel | None = None
        self._solo_model: StandingsListModel | None = None

        self.stale_infobar = self._build_infobar()
        # The one note seam's two facts: E7.3.2's stale-export flag is
        # driven by the presenter (``set_stale``), E6.4.3's self-test
        # note is read here once -- it describes the ride's last finish,
        # and a window is rebuilt when that changes.
        self._stale = False
        self._self_test_unverified = bool(data_source.results_self_test_unverified())

        self.presenter = ResultsPresenter(
            self,
            data_source,
            tiebreak_order=tiebreak_order,
            export_watermark=export_watermark,
            show_dns_riders=show_dns_riders,
        )
        # E7.3.2: app.py's export completion (and E6.4.2's own
        # ``_export_options``) finds the open window's presenter through
        # ``wx.FindWindowByName(RESULTS_DLG).presenter`` -- the E6.4.2
        # contract this view was always meant to fulfil. The wxPython
        # wrapper keeps the attribute alive: the window's own event
        # bindings hold this view, and a closed window's lookup
        # returns None, so the seam self-clears.
        self.dialog.presenter = self.presenter

        self._bind_events()
        self._apply_min_size()

    def _build_columns(self) -> None:
        """Build every standings list's seven columns (Part 2/G6/R-14).

        The Team list drops its Plate column under ``RIDER_POOLED``
        (Part 2): a pooled team's plate is derived from its members, so
        that column repeats a member's plate rather than naming the
        entry, and the Entry column already carries the team's name.
        The standalone and Solo lists always keep theirs, as does the
        Team list under ``TEAM_RELAY``. With no Plate column to carry
        the table's width, the team list's Hand column takes it
        (:data:`TEAM_HAND_WIDTH`).
        """
        hide_team_plate = self.plate_model is PlateModel.RIDER_POOLED
        team_hand_width = TEAM_HAND_WIDTH if hide_team_plate else HAND_WIDTH
        self._build_columns_for(self.standings_list)
        self._build_columns_for(
            self.teams_standings_list, hide_plate=hide_team_plate, hand_width=team_hand_width
        )
        self._build_columns_for(self.solo_standings_list)

    @staticmethod
    def _build_columns_for(
        control: Any,  # noqa: ANN401 -- wx ships no stubs
        *,
        hide_plate: bool = False,
        hand_width: int = HAND_WIDTH,
    ) -> None:
        """Append one list's seven columns in canvas order.

        The two card columns (Best 5, Draw) are built by hand with the
        markup renderer -- each card in the cell takes its own suit
        colour (:data:`MARKUP_COLUMNS`,
        :func:`~rivercrossing.ui.views._support.append_markup_column`);
        every other column is plain text.

        Args:
            control: The ``DataViewCtrl`` to append to.
            hide_plate: Hide the Plate column (a pooled team's list).
            hand_width: The Hand column's pinned width; the team list
                widens it when it hides Plate
                (:data:`TEAM_HAND_WIDTH`), every other list uses the
                solo pin.
        """
        widths = list(COLUMN_WIDTHS)
        widths[COL_HAND] = hand_width
        columns = [
            append_markup_column(
                control, label, col, width=widths[col], flags=STANDINGS_COLUMN_FLAGS
            )
            if col in MARKUP_COLUMNS
            else control.AppendTextColumn(
                label, col, width=widths[col], flags=STANDINGS_COLUMN_FLAGS
            )
            for col, label in enumerate(COLUMN_LABELS)
        ]
        if hide_plate:
            columns[COL_PLATE].SetHidden(True)  # noqa: FBT003 -- wx API takes a positional bool

    def _build_infobar(self) -> Any:  # noqa: ANN401 -- wx ships no stubs
        """Build the code-side :data:`STALE_INFOBAR`, inserted on top.

        Mirrors ``main_frame.MainFrame._build_infobar``'s measured
        slide-effect hang fix: both effects disabled, so a later
        ``ShowMessage()``/``Dismiss()`` (E7.3.2's trigger) returns.
        results.xrc reserves sizer index 0 (a zero-size spacer) for
        this bar; inserting at index 0 displaces the spacer.
        """
        bar = wx.InfoBar(self.dialog)
        bar.SetName(STALE_INFOBAR)
        bar.SetShowHideEffects(wx.SHOW_EFFECT_NONE, wx.SHOW_EFFECT_NONE)
        self.dialog.GetSizer().Insert(0, bar, 0, wx.EXPAND)
        return bar

    def _bind_events(self) -> None:
        """Forward each standings list's activations.

        Each standings list explains its own ⚠ rows on the activation
        gesture (Part 1). The publish checkboxes that once bound here
        left the dialog for the Results menu (G6), so the dialog
        itself binds nothing.
        """
        for control in (
            self.standings_list,
            self.teams_standings_list,
            self.solo_standings_list,
        ):
            control.Bind(wx.dataview.EVT_DATAVIEW_ITEM_ACTIVATED, self._on_standings_activated)

    def _on_standings_activated(self, event: Any) -> None:  # noqa: ANN401 -- wx ships no stubs
        """Explain the activated row's ⚠ badge (Part 1).

        Double-clicking (or pressing Enter on) a ``draw_required`` row
        opens the OK-only information alert carrying the row's own tie
        note and :data:`DRAW_EXPLANATION`; any other row does nothing.
        The row resolves through the model the event was raised with --
        the same model the list is rendering -- so a list sharing this
        handler cannot explain another list's row.
        """
        model = event.GetModel()
        if not isinstance(model, StandingsListModel):
            return
        standing = model.standing_at(model.GetRow(event.GetItem()))
        if standing is None or not standing.draw_required:
            return
        show_info(self.dialog, DRAW_INFO_TITLE, draw_info_message(standing))

    def show_standings(self, teams: list[StandingsRow], solo: list[StandingsRow]) -> None:
        """Render the standings (``ResultsView``, Phase 3).

        A MIXED ride associates a flat model over *teams* with
        ``teams_standings_list`` and one over *solo* with
        ``solo_standings_list``, then shows the notebook and hides the
        standalone list. A SOLO ride associates *solo* with
        ``standings_list`` and hides the notebook. The list that is not
        in use is hidden so its sizer slot collapses.

        See ``ui.views._support.associate_model``'s docstring for
        why this repaints explicitly (unverified remedy).
        """
        if self.entry_mode is EntryMode.MIXED:
            self._teams_model = StandingsListModel(teams, theme.card_red())
            self._solo_model = StandingsListModel(solo, theme.card_red())
            associate_model(self.teams_standings_list, self._teams_model)
            associate_model(self.solo_standings_list, self._solo_model)
            self.standings_list.Hide()
            self.results_notebook.Show()
        else:
            self._model = StandingsListModel(solo, theme.card_red())
            associate_model(self.standings_list, self._model)
            self.results_notebook.Hide()
            self.standings_list.Show()
        self.dialog.Layout()

    def set_stale(self, *, stale: bool) -> None:
        """Show/hide :data:`STALE_INFOBAR` (``ResultsView``, E6.4.1).

        Hidden by default; E7.3.2 shows it after reopened corrections
        and clears it on re-export. ``wx.InfoBar`` starts hidden
        (measured), so constructing the bar is all the "hidden" state
        needs. The bar is the dialog's one note seam, so
        :meth:`_sync_banner` also carries E6.4.3's self-test note when
        the ride's results are unverified.
        """
        self._stale = stale
        self._sync_banner()

    def _sync_banner(self) -> None:
        """Apply the two note facts to the one code-side InfoBar.

        Stale results take precedence: that is the actionable warning
        (re-export refreshes them), while the self-test note is a
        standing fact about how the ride was closed. Neither is true,
        the bar dismisses.
        """
        if self._stale:
            self.stale_infobar.ShowMessage(STALE_NOTE, wx.ICON_WARNING)
        elif self._self_test_unverified:
            self.stale_infobar.ShowMessage(SELF_TEST_NOTE, wx.ICON_WARNING)
        else:
            self.stale_infobar.Dismiss()
        self.dialog.Layout()

    def _apply_min_size(self) -> None:
        """Force the measured width floor, then Fit() the rest (D16).

        Each standings list is floored to hold
        :data:`STANDINGS_MIN_ROWS` rows -- the MIXED notebook's two
        pages and the SOLO standalone list alike -- before the dialog
        measures itself, so Fit() sees the floored children and every
        list shows ten rows without a scrollbar.

        See :meth:`ride_library.RideLibrary._apply_min_size`'s
        docstring for the measured ``SetMinSize`` + ``Fit()``
        reasoning this mirrors.
        """
        for control in (
            self.standings_list,
            self.teams_standings_list,
            self.solo_standings_list,
        ):
            control.SetMinSize(wx.Size(-1, STANDINGS_LIST_MIN_HEIGHT))
        self.dialog.SetMinSize(wx.Size(MIN_SIZE[0], -1))
        self.dialog.Fit()
