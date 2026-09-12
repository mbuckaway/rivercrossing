# SPDX-License-Identifier: GPL-3.0-only
"""Results presenter -- results_dlg (1f), standings and publishing.

Pure Python -- no ``wx`` import may ever land here (R-71).

The presenter ranks the live standings with the ride's stored
tie-break order (``RideConfig.tiebreak_order``, set in Ride Setup) and
drives :meth:`ResultsView.show_standings` with the data source's
teams/solo split. It also builds the ``ExportOptions`` the export
handlers read through :meth:`ResultsPresenter.export_options`.

E7.3.2 (the stale-export flag) adds the live channel: the presenter
holds the engine event count captured at the last export (the *export
watermark*, passed in at construction and advanced by
:meth:`ResultsPresenter.mark_exported`), and on the first render asks
the data source whether a correction event landed at/after that
watermark (:meth:`DataSource.results_stale`), then drives
:meth:`ResultsView.set_stale` -- ``True`` when published results are
stale, ``False`` on a fresh export or when no correction landed since.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import DEFAULT_TIEBREAK_ORDER
from rivercrossing.standings import tiebreak_order_from_spellings

if TYPE_CHECKING:
    from rivercrossing.ui.presenters.data_source import DataSource, StandingsRow

__all__ = ["ResultsPresenter", "ResultsView"]


@runtime_checkable
class ResultsView(Protocol):
    """View surface for the results window (results_dlg, 1f)."""

    def show_standings(self, teams: list[StandingsRow], solo: list[StandingsRow]) -> None:
        """Render the teams and solo standings sections."""
        ...

    def set_stale(self, *, stale: bool) -> None:
        """Show/hide stale_infobar after post-export corrections."""
        ...

    def show_publish_options(self, options: ExportOptions) -> None:
        """Reflect the publish checkboxes (show_times_chk and peers)."""
        ...

    def publish_options(self) -> ExportOptions:
        """Return the current publish-checkbox states."""
        ...


class ResultsPresenter:
    """Presenter for the results window (results_dlg, 1f).

    Ranks the standings with the ride's stored tie-break order and
    holds the ``ExportOptions`` the export handlers read. E7.3.2 adds
    the stale-export flag: the presenter holds the export watermark
    and drives :meth:`ResultsView.set_stale` on the first render.
    """

    def __init__(  # noqa: PLR0913 -- (view, data_source) + the tie-break order and export-watermark seams
        self,
        view: ResultsView,
        data_source: DataSource,
        *,
        tiebreak_order: tuple[str, str, str] = DEFAULT_TIEBREAK_ORDER,
        export_watermark: int | None = None,
    ) -> None:
        """Store the view/source, rank the standings, evaluate stale.

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
        self._options = ExportOptions()
        self._export_watermark = export_watermark

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
