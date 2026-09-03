# SPDX-License-Identifier: GPL-3.0-only
"""Results presenter -- results_frame (1f), standings and publishing.

Pure Python -- no ``wx`` import may ever land here (R-71).

E6.4.1 (P9) makes the presenter live: it owns the tie-break label
map, seeds ``tiebreak_list`` from the ride's stored
``tiebreak_order``, re-ranks ``standings(order=...)`` live on a
reorder, restores the last-known-good order (and posts a notice) when
a reorder carries an unrecognised label or a wrong row count, and
builds the ``ExportOptions`` the export handlers (E6.4.2's menu task)
read through :meth:`ResultsPresenter.export_options`.

E7.3.2 (the stale-export flag) adds the second live channel: the
presenter holds the engine event count captured at the last export
(the *export watermark*, passed in at construction and advanced by
:meth:`ResultsPresenter.mark_exported`), and on every render asks the
data source whether a correction event landed at/after that watermark
(:meth:`DataSource.results_stale`), then drives
:meth:`ResultsView.set_stale` -- ``True`` when published results are
stale, ``False`` on a fresh export or when no correction landed since.

The label map is duplicated from ``ride_setup.RideSetup``'s own
``_TIEBREAK_LABELS`` (the brief's own "two uses is below the
rule-of-three; do NOT refactor ride_setup in this task" -- the plain
labels, never a ``①`` rank prefix, which would go stale after a
reorder).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import (
    DEFAULT_TIEBREAK_ORDER,
    TIEBREAK_HIGH_CARD,
    TIEBREAK_LAPS,
)
from rivercrossing.ride import TIEBREAK_TOTAL_TIME as _TIEBREAK_TOTAL_TIME
from rivercrossing.standings import tiebreak_order_from_spellings

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import DataSource, StandingsRow

__all__ = ["ResultsPresenter", "ResultsView"]

# tiebreak_list's plain-label vocabulary (module docstring) -- the
# same spelling->label map ride_setup.RideSetup owns for setup.xrc's
# tiebreak_list, duplicated here per the brief's rule-of-three note.
_TIEBREAK_LABELS: dict[str, str] = {
    TIEBREAK_LAPS: "Most laps",
    _TIEBREAK_TOTAL_TIME: "Total time",
    TIEBREAK_HIGH_CARD: "High-card draw",
}
_TIEBREAK_IDS_BY_LABEL: dict[str, str] = {label: id_ for id_, label in _TIEBREAK_LABELS.items()}

_UNRECOGNISED_ORDER_NOTICE = "Unrecognised tie-break order — restored"


@runtime_checkable
class ResultsView(Protocol):
    """View surface for the results window (results_frame, 1f)."""

    def show_standings(self, teams: list[StandingsRow], solo: list[StandingsRow]) -> None:
        """Render standings_list's two sections (teams, then solo)."""
        ...

    def set_stale(self, *, stale: bool) -> None:
        """Show/hide stale_infobar after post-export corrections."""
        ...

    def show_publish_options(self, options: ExportOptions) -> None:
        """Reflect the publish checkboxes (show_times_chk and peers)."""
        ...

    # E6.4.1: the three members the live presenter actually calls --
    # the same "add the member once the presenter calls it" precedent
    # main_frame.py's own docstring records for set_hide_times.
    def set_tiebreak_labels(self, labels: list[str]) -> None:
        """Seed tiebreak_list's plain-label rows (or restore them)."""
        ...

    def show_notice(self, text: str) -> None:
        """Show a transient status notice (an unrecognised reorder)."""
        ...

    def publish_options(self) -> ExportOptions:
        """Return the current publish-checkbox states."""
        ...


class ResultsPresenter:
    """Presenter for the results window (results_frame, 1f).

    E6.4.1 (P9) replaces the E1.2.3 no-op: the presenter seeds the
    tie-break list from the ride's stored order, re-ranks live on a
    reorder (converting plain labels back onto ``TieBreak`` members
    through its own label map), restores the last-known-good order on
    an unrecognised reorder, and holds the ``ExportOptions`` the
    export handlers read. E7.3.2 adds the stale-export flag: the
    presenter holds the export watermark and drives
    :meth:`ResultsView.set_stale` on every render.
    """

    def __init__(  # noqa: PLR0913 -- (view, data_source) + the tie-break order and export-watermark seams
        self,
        view: ResultsView,
        data_source: DataSource,
        *,
        tiebreak_order: tuple[str, str, str] = DEFAULT_TIEBREAK_ORDER,
        export_watermark: int | None = None,
    ) -> None:
        """Store the view/source, seed the tie-break list, first render.

        Args:
            view: The results view this presenter drives.
            data_source: The read-only display-data seam.
            tiebreak_order: The ride's stored tie-break spellings, in
                priority order (``RideConfig.tiebreak_order``);
                defaults to R-14's order.
            export_watermark: The engine event count captured at the
                last export (E7.3.2); ``None`` when nothing was
                exported. The first render evaluates the stale flag
                against it.
        """
        self.view = view
        self.data_source = data_source
        self._order = tiebreak_order_from_spellings(tiebreak_order)
        self._last_good_labels = [_TIEBREAK_LABELS[spelling] for spelling in tiebreak_order]
        self._options = ExportOptions()
        self._export_watermark = export_watermark

        self.view.set_tiebreak_labels(list(self._last_good_labels))
        teams, solo = self.data_source.standings(order=self._order)
        self.view.show_standings(teams, solo)
        self._sync_stale()

    @property
    def export_watermark(self) -> int | None:
        """Return the engine event count at the last export (E7.3.2).

        ``None`` until a fresh export records one; advanced by
        :meth:`mark_exported` (the app's export completion).
        """
        return self._export_watermark

    def mark_exported(self, watermark: int) -> None:
        """Record a fresh export's watermark and clear the stale flag.

        E7.3.2's export-completion seam: the app calls this (on an
        open results window's presenter) after a successful export so
        the banner clears immediately, and the next refresh compares
        against the advanced watermark.
        """
        self._export_watermark = watermark
        self.view.set_stale(stale=False)

    def on_tiebreak_reordered(self, labels: list[str]) -> None:
        """Handle a tiebreak_list reorder: re-rank live (E6.4.1).

        *labels* are the control's current plain-label rows, in the
        order the operator left them. Exactly the three known labels
        convert onto a ``TieBreak`` order and re-rank the standings
        through ``standings(order=...)``; anything else -- an
        unrecognised label (a New button typed a foreign row) or a
        wrong row count (a Delete removed one) -- restores the
        last-known-good order and posts a notice, never a crash. The
        same New/Delete gap ride_setup.py's own docstring records.

        A reorder is a refresh, so the stale flag is re-evaluated too
        (E7.3.2): a correction that landed while the window sat open
        shows on the next interaction.
        """
        if len(labels) != len(DEFAULT_TIEBREAK_ORDER):
            self._restore_tiebreak()
            return
        try:
            spellings = tuple(_TIEBREAK_IDS_BY_LABEL[label] for label in labels)
        except KeyError:
            self._restore_tiebreak()
            return
        self._order = tiebreak_order_from_spellings(spellings)
        self._last_good_labels = list(labels)
        teams, solo = self.data_source.standings(order=self._order)
        self.view.show_standings(teams, solo)
        self._sync_stale()

    def on_publish_toggled(self) -> None:
        """Handle a publish-checkbox toggle: rebuild the held options.

        Reads the view's five checkbox states and holds the resulting
        :class:`~rivercrossing.htmlexport.ExportOptions` for E6.4.2's
        export handlers.
        """
        self._options = self.view.publish_options()

    def export_options(self) -> ExportOptions:
        """Return the options the last toggle produced (E6.4.2 seam)."""
        return self._options

    def _restore_tiebreak(self) -> None:
        """Restore the last-known-good tie-break rows and notice."""
        self.view.set_tiebreak_labels(list(self._last_good_labels))
        self.view.show_notice(_UNRECOGNISED_ORDER_NOTICE)

    def _sync_stale(self) -> None:
        """Re-evaluate and apply the stale-export flag (E7.3.2).

        Asks the data source whether a correction event landed at/after
        the held export watermark and drives
        :meth:`ResultsView.set_stale` with the answer -- the one place
        the banner's state is decided.
        """
        # Pre-E7.3.2 sources (e.g. the E6-era functional stubs) carry
        # no results_stale member -- never stale for them (no watermark)
        # because they hold no watermark either.
        query = getattr(self.data_source, "results_stale", None)
        stale = bool(query(self._export_watermark)) if query is not None else False
        self.view.set_stale(stale=stale)
