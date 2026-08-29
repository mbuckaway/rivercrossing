# SPDX-License-Identifier: GPL-3.0-only
"""Results presenter unit tests (P9 / E6.4.1), tests-first (R-70).

``ResultsPresenter`` goes live in E6.4.1: it owns the tie-break
label map, seeds ``tiebreak_list`` from the ride's stored
``tiebreak_order``, re-ranks ``standings(order=...)`` live on a
reorder, restores the last-good order on an unrecognised reorder, and
builds the ``ExportOptions`` the export handlers (E6.4.2) read. The
presenter is pure Python (R-71), so every test drives it with a
recording fake view and a recording ``DataSource`` -- no wx and no
real engine are needed. ``RecordingResultsSource`` subclasses
:class:`~rivercrossing.ui.presenters.data_source.EmptyDataSource` so
it structurally satisfies ``DataSource`` (every Protocol member)
while overriding only ``standings`` to record the orders it was
called with.
"""

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import (
    DEFAULT_TIEBREAK_ORDER as RIDE_DEFAULT_ORDER,
)
from rivercrossing.ride import TIEBREAK_HIGH_CARD, TIEBREAK_LAPS, TIEBREAK_TOTAL_TIME
from rivercrossing.standings import DEFAULT_TIEBREAK_ORDER, TieBreak
from rivercrossing.ui.presenters import ResultsPresenter, StandingsRow
from rivercrossing.ui.presenters import results as results_module
from rivercrossing.ui.presenters.data_source import EmptyDataSource

# ------------------------------------------------------------- fixtures


class RecordingResultsView:
    """A complete ``ResultsView`` spy recording each call, in order.

    ``set_stale`` is a no-op: E7.3.2 (the stale-export flag trigger)
    drives it, not this presenter, so nothing here ever calls it.
    ``publish_options`` returns whatever the test last handed to
    ``show_publish_options`` -- the fake "checkbox states" the
    presenter reads.
    """

    def __init__(self) -> None:
        """Start every channel empty."""
        self.shown_rows: list[StandingsRow] = []
        self.tiebreak_labels: list[str] = []
        self.notices: list[str] = []
        self.reported_options = ExportOptions()
        self.publish_reads = 0

    def show_standings(self, rows: list[StandingsRow]) -> None:
        """Record the rendered standings rows."""
        self.shown_rows = list(rows)

    def set_stale(self, *, stale: bool) -> None:
        """No-op: nothing in this presenter calls it."""

    def show_publish_options(self, options: ExportOptions) -> None:
        """Record the reflected checkbox states."""
        self.reported_options = options

    def set_tiebreak_labels(self, labels: list[str]) -> None:
        """Record the seeded/restored tiebreak_list rows."""
        self.tiebreak_labels = list(labels)

    def show_notice(self, text: str) -> None:
        """Record one status notice."""
        self.notices.append(text)

    def publish_options(self) -> ExportOptions:
        """Return the recorded checkbox states, and count the read."""
        self.publish_reads += 1
        return self.reported_options


class RecordingResultsSource(EmptyDataSource):
    """A full ``DataSource`` recording every ``standings`` order.

    Subclasses ``EmptyDataSource`` (which implements every Protocol
    member) so this is a real ``DataSource``, overriding only
    ``standings`` to record its orders and return whatever rows a test
    pre-loads.
    """

    def __init__(self) -> None:
        """Start with no pre-loaded rows and no recorded orders."""
        super().__init__()
        self.standings_orders: list[tuple[TieBreak, ...]] = []
        self.rows_by_order: dict[tuple[TieBreak, ...], list[StandingsRow]] = {}

    def standings(
        self, order: tuple[TieBreak, ...] = DEFAULT_TIEBREAK_ORDER
    ) -> list[StandingsRow]:
        """Record *order*, then return the rows pre-loaded for it."""
        self.standings_orders.append(order)
        return self.rows_by_order.get(order, [])


def _row(plate: str, *, place: int = 1) -> StandingsRow:
    """Build one minimal standings row for *plate*."""
    return StandingsRow(
        place=place,
        plate=plate,
        entry=f"Rider {plate}",
        laps=1,
        total="0:01:00",
        best5=(),
        hand="High Card — Ace",
    )


# ------------------------------------------------------- construction


@pytest.mark.parametrize(
    ("stored", "labels"),
    [
        (RIDE_DEFAULT_ORDER, ["Most laps", "Total time", "High-card draw"]),
        (("total_time", "laps", "high_card"), ["Total time", "Most laps", "High-card draw"]),
        (("high_card", "total_time", "laps"), ["High-card draw", "Total time", "Most laps"]),
    ],
    ids=["default", "time_first", "draw_first"],
)
def test_results_presenter_init_seeds_the_tiebreak_list_from_the_stored_order(
    stored: tuple[str, str, str], labels: list[str]
) -> None:
    """``set_tiebreak_labels`` is called once with the mapped labels."""
    view = RecordingResultsView()
    source = RecordingResultsSource()

    ResultsPresenter(view, source, tiebreak_order=stored)

    assert view.tiebreak_labels == labels


def test_results_presenter_init_renders_the_initial_standings_with_the_stored_order() -> None:
    """The first standings render uses the ride's order, not default."""
    source = RecordingResultsSource()
    converted = (TieBreak.TOTAL_TIME, TieBreak.MOST_LAPS, TieBreak.HIGH_CARD_DRAW)
    source.rows_by_order[converted] = [_row("7")]
    view = RecordingResultsView()

    ResultsPresenter(view, source, tiebreak_order=("total_time", "laps", "high_card"))

    assert source.standings_orders == [converted]
    assert view.shown_rows == [_row("7")]


def test_results_presenter_holds_the_view_and_data_source_it_was_given() -> None:
    """E6.4.1 keeps the E1.2.3 ``(view, data_source)`` shape."""
    view = RecordingResultsView()
    source = RecordingResultsSource()

    presenter = ResultsPresenter(view, source)

    assert presenter.view is view
    assert presenter.data_source is source


# --------------------------------------------------- live re-ranking


def test_on_tiebreak_reordered_given_valid_labels_re_ranks_and_shows_rows() -> None:
    """A valid reorder re-ranks with the converted order, live."""
    view = RecordingResultsView()
    source = RecordingResultsSource()
    presenter = ResultsPresenter(view, source)
    reordered = (TieBreak.TOTAL_TIME, TieBreak.MOST_LAPS, TieBreak.HIGH_CARD_DRAW)
    source.rows_by_order[reordered] = [_row("34", place=1)]

    presenter.on_tiebreak_reordered(["Total time", "Most laps", "High-card draw"])

    assert source.standings_orders[-1] == reordered
    assert view.shown_rows == [_row("34", place=1)]


def test_on_tiebreak_reordered_given_an_unknown_label_restores_and_notices() -> None:
    """An unrecognised label cannot be converted -- restore + notice."""
    view = RecordingResultsView()
    source = RecordingResultsSource()
    presenter = ResultsPresenter(view, source)
    source.standings_orders.clear()

    presenter.on_tiebreak_reordered(["Most laps", "Bogus criterion", "High-card draw"])

    assert view.tiebreak_labels == ["Most laps", "Total time", "High-card draw"]
    assert view.notices == ["Unrecognised tie-break order — restored"]
    assert source.standings_orders == []


def test_on_tiebreak_reordered_given_a_wrong_row_count_restores_and_notices() -> None:
    """A New/Delete-mangled row set is invalid -- restore + notice."""
    view = RecordingResultsView()
    source = RecordingResultsSource()
    presenter = ResultsPresenter(view, source)
    source.standings_orders.clear()

    presenter.on_tiebreak_reordered(["Most laps", "Total time"])

    assert view.tiebreak_labels == ["Most laps", "Total time", "High-card draw"]
    assert view.notices == ["Unrecognised tie-break order — restored"]
    assert source.standings_orders == []


def test_on_tiebreak_reordered_restores_the_latest_good_order_after_a_success() -> None:
    """Last-good advances on success, so a later failure restores it."""
    view = RecordingResultsView()
    source = RecordingResultsSource()
    presenter = ResultsPresenter(view, source)
    presenter.on_tiebreak_reordered(["Total time", "Most laps", "High-card draw"])
    view.tiebreak_labels.clear()

    presenter.on_tiebreak_reordered(["Total time", "Bogus criterion", "High-card draw"])

    assert view.tiebreak_labels == ["Total time", "Most laps", "High-card draw"]


# -------------------------------------------------- publish options


def test_on_publish_toggled_builds_export_options_from_the_view_checkboxes() -> None:
    """The stored options equal the checkbox states the view reports."""
    view = RecordingResultsView()
    view.reported_options = ExportOptions(
        show_times=True,
        laps_board=False,
        time_board=True,
        full_field=False,
        all_cards=False,
    )
    presenter = ResultsPresenter(view, RecordingResultsSource())

    presenter.on_publish_toggled()

    assert presenter.export_options() == view.reported_options
    assert view.publish_reads == 1


def test_export_options_defaults_to_the_canvas_flags_before_any_toggle() -> None:
    """Times off, laps board on, time board off, cards on."""
    presenter = ResultsPresenter(RecordingResultsView(), RecordingResultsSource())

    assert presenter.export_options() == ExportOptions()


# --------------------------------------------- label-map invariant


@given(order=st.permutations([TIEBREAK_LAPS, TIEBREAK_TOTAL_TIME, TIEBREAK_HIGH_CARD]))
def test_tiebreak_label_map_round_trips_any_stored_order(order: list[str]) -> None:
    """Invariant: labels -> spellings is the exact inverse of the seed.

    The presenter's own 3-entry map is the only converter both the
    seed and the reorder path share; this pins that label conversion
    is invertible for every permutation of the stored spellings.
    """
    labels = [results_module._TIEBREAK_LABELS[spelling] for spelling in order]

    assert [results_module._TIEBREAK_IDS_BY_LABEL[label] for label in labels] == order
