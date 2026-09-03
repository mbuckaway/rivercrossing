# SPDX-License-Identifier: GPL-3.0-only
"""Results presenter unit tests (E6.4.1 + E7.3.2), tests-first (R-70).

``ResultsPresenter`` goes live in E6.4.1: it owns the tie-break
label map, seeds ``tiebreak_list`` from the ride's stored
``tiebreak_order``, re-ranks ``standings(order=...)`` live on a
reorder, restores the last-good order on an unrecognised reorder, and
builds the ``ExportOptions`` the export handlers (E6.4.2) read.
E7.3.2 (the stale-export flag) adds the second live channel: the
presenter holds the engine event count captured at the last export
(the export watermark) and, on every refresh, asks the data source
whether a correction event landed at/after that watermark, then
drives ``ResultsView.set_stale`` -- ``True`` when published results
are stale, ``False`` on a fresh export (``mark_exported``) or when no
correction landed since.

The presenter is pure Python (R-71), so every test drives it with a
recording fake view and a recording ``DataSource`` -- no wx and no
real engine are needed. ``RecordingResultsSource`` subclasses
:class:`~rivercrossing.ui.presenters.data_source.EmptyDataSource` so
it structurally satisfies ``DataSource`` (every Protocol member)
while overriding ``standings`` to record the orders it was called
with and ``results_stale`` to answer the stale-export query with a
test-configured value.
"""

from datetime import date, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from rivercrossing.cards import Shoe
from rivercrossing.htmlexport import ExportOptions
from rivercrossing.ride import (
    DEFAULT_TIEBREAK_ORDER as RIDE_DEFAULT_ORDER,
)
from rivercrossing.ride import (
    TIEBREAK_HIGH_CARD,
    TIEBREAK_LAPS,
    TIEBREAK_TOTAL_TIME,
    Event,
    RideConfig,
    RideEngine,
)
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.standings import DEFAULT_TIEBREAK_ORDER, TieBreak
from rivercrossing.ui.presenters import ResultsPresenter, StandingsRow
from rivercrossing.ui.presenters import results as results_module
from rivercrossing.ui.presenters.data_source import (
    CORRECTION_ACTIONS,
    EmptyDataSource,
    EngineDataSource,
    has_correction_since,
)

# ------------------------------------------------------------- fixtures


class RecordingResultsView:
    """A complete ``ResultsView`` spy recording each call, in order.

    ``set_stale`` records every stale-flag update the presenter applies
    (E7.3.2) -- ``stale_calls`` is the assertion surface for the
    stale-export flag. ``publish_options`` returns whatever the test
    last handed to ``show_publish_options`` -- the fake "checkbox
    states" the presenter reads.
    """

    def __init__(self) -> None:
        """Start every channel empty."""
        self.shown_rows: list[StandingsRow] = []
        self.tiebreak_labels: list[str] = []
        self.notices: list[str] = []
        self.reported_options = ExportOptions()
        self.publish_reads = 0
        self.stale_calls: list[bool] = []

    def show_standings(self, rows: list[StandingsRow]) -> None:
        """Record the rendered standings rows."""
        self.shown_rows = list(rows)

    def set_stale(self, *, stale: bool) -> None:
        """Record one stale-flag update (True shows the banner)."""
        self.stale_calls.append(stale)

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
    member) so this is a real ``DataSource``, overriding ``standings``
    to record its orders and return whatever rows a test pre-loads,
    and ``results_stale`` (E7.3.2) to record the watermark it is
    queried with and return a test-configured answer.
    """

    def __init__(self) -> None:
        """Start with no pre-loaded rows and no recorded orders."""
        super().__init__()
        self.standings_orders: list[tuple[TieBreak, ...]] = []
        self.rows_by_order: dict[tuple[TieBreak, ...], list[StandingsRow]] = {}
        self.stale_result: bool = False
        self.stale_queries: list[int | None] = []

    def standings(
        self, order: tuple[TieBreak, ...] = DEFAULT_TIEBREAK_ORDER
    ) -> list[StandingsRow]:
        """Record *order*, then return the rows pre-loaded for it."""
        self.standings_orders.append(order)
        return self.rows_by_order.get(order, [])

    def results_stale(self, export_watermark: int | None) -> bool:
        """Record the watermark query; return the configured answer."""
        self.stale_queries.append(export_watermark)
        return self.stale_result


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


# --------------------------------------- E7.3.2 stale-export flag


def test_results_presenter_init_clears_the_stale_flag_before_any_export() -> None:
    """No export watermark: nothing published, so nothing is stale."""
    view = RecordingResultsView()
    source = RecordingResultsSource()

    ResultsPresenter(view, source)

    assert view.stale_calls == [False]
    assert source.stale_queries == [None]


def test_results_presenter_init_marks_stale_when_a_correction_landed_past_the_watermark() -> None:
    """A post-export correction trips the flag at open time."""
    view = RecordingResultsView()
    source = RecordingResultsSource()
    source.stale_result = True

    ResultsPresenter(view, source, export_watermark=3)

    assert view.stale_calls == [True]
    assert source.stale_queries == [3]


def test_results_presenter_init_stays_clean_when_no_correction_since_the_watermark() -> None:
    """No post-export correction: the flag stays clear."""
    view = RecordingResultsView()
    source = RecordingResultsSource()

    ResultsPresenter(view, source, export_watermark=3)

    assert view.stale_calls == [False]
    assert source.stale_queries == [3]


def test_results_presenter_mark_exported_advances_the_watermark_and_clears_stale() -> None:
    """A fresh export records the watermark and clears the banner."""
    view = RecordingResultsView()
    source = RecordingResultsSource()
    source.stale_result = True
    presenter = ResultsPresenter(view, source, export_watermark=2)

    presenter.mark_exported(6)

    assert presenter.export_watermark == 6
    assert view.stale_calls == [True, False]


def test_results_presenter_refresh_re_evaluates_stale_after_a_post_export_correction() -> None:
    """A refresh sees a correction that landed since the watermark."""
    view = RecordingResultsView()
    source = RecordingResultsSource()
    presenter = ResultsPresenter(view, source, export_watermark=2)
    source.stale_result = True

    presenter.on_tiebreak_reordered(["Total time", "Most laps", "High-card draw"])

    assert view.stale_calls == [False, True]
    assert source.stale_queries == [2, 2]


def test_results_presenter_mark_exported_then_a_later_correction_marks_stale_again() -> None:
    """A correction after re-export trips the flag again."""
    view = RecordingResultsView()
    source = RecordingResultsSource()
    presenter = ResultsPresenter(view, source, export_watermark=2)
    presenter.mark_exported(6)
    source.stale_result = True

    presenter.on_tiebreak_reordered(["Total time", "Most laps", "High-card draw"])

    assert presenter.export_watermark == 6
    assert view.stale_calls == [False, False, True]
    assert source.stale_queries[-1] == 6


# --------------------------- E7.3.2 correction-vs-watermark helper


def test_correction_actions_is_the_e7_audited_correction_vocabulary() -> None:
    """The stale set is the E7.2.1 corrections plus void_crossing."""
    assert (
        frozenset(
            {
                "add_crossing_at",
                "edit_crossing",
                "reassign",
                "deal_manual",
                "dnf",
                "void_card",
                "void_crossing",
            }
        )
        == CORRECTION_ACTIONS
    )
    assert "record_crossing" not in CORRECTION_ACTIONS
    assert "undo" not in CORRECTION_ACTIONS
    assert "set_start_time" not in CORRECTION_ACTIONS


def _event(action: str) -> Event:
    """Build one minimal event with *action* and an empty payload."""
    return Event(action=action, payload={})


def test_has_correction_since_without_a_watermark_is_never_stale() -> None:
    """No export yet: nothing published, never stale."""
    events = (_event("start"), _event("edit_crossing"))

    assert has_correction_since(events, None) is False


def test_has_correction_since_flags_a_correction_exactly_at_the_watermark() -> None:
    """A correction at index == watermark landed after the export."""
    events = (_event("start"), _event("record_crossing"), _event("edit_crossing"))

    assert has_correction_since(events, 2) is True


def test_has_correction_since_with_the_watermark_at_the_event_count_is_clean() -> None:
    """A watermark at the current count: no events after it."""
    events = (_event("start"), _event("record_crossing"), _event("edit_crossing"))

    assert has_correction_since(events, len(events)) is False
    assert has_correction_since(events, len(events) + 1) is False


def test_has_correction_since_ignores_non_correction_events_after_the_watermark() -> None:
    """A live-entry tail (record_crossing) is not a correction."""
    events = (_event("start"), _event("edit_crossing"), _event("record_crossing"))

    assert has_correction_since(events, 0) is True
    assert has_correction_since(events, 2) is False


@given(
    actions=st.lists(
        st.sampled_from((*CORRECTION_ACTIONS, "record_crossing", "start")),
        max_size=8,
    ),
    watermark=st.integers(min_value=0, max_value=9),
)
def test_has_correction_since_matches_a_correction_scan_past_the_watermark(
    actions: list[str], watermark: int
) -> None:
    """Invariant: True iff a correction sits at/after the watermark."""
    events = tuple(_event(action) for action in actions)
    expected = any(action in CORRECTION_ACTIONS for action in actions[watermark:])

    assert has_correction_since(events, watermark) is expected


# ------------------------- E7.3.2 live EngineDataSource delegation


def _engine_source_with_correction() -> tuple[RideEngine, EngineDataSource]:
    """Build a RUNNING engine with start, one crossing, and an edit.

    The clock is a fixed naive instant (the ``_FakeClock`` convention
    test_lists_results.py uses): the engine's lap arithmetic subtracts
    naive timestamps, so an aware clock would TypeError against the
    naive correction instants (the latent bug test_corrections.py's
    own builder carries, recorded in this task's report).
    """
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(first_name="Rider", last_name="12", plate="12")
    config = RideConfig(
        name="GORBA EPIC 2026",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive, RideConfig's own contract
        planned_duration_s=21600,
        min_lap_s=1,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
    )
    shoe = Shoe(decks=config.deck_count, jokers_per_deck=config.jokers_per_deck, seed=20260920)
    engine = RideEngine(
        config=config,
        shoe=shoe,
        clock=lambda: datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive clock, matching the naive crossing instants
        roster=roster,
    )
    engine.start()
    engine.record_crossing("12", at=datetime(2026, 9, 20, 10, 30))  # noqa: DTZ001
    engine.edit_crossing("12", 1, datetime(2026, 9, 20, 10, 31), reason="mis-key")  # noqa: DTZ001
    return engine, EngineDataSource(engine, roster)


def test_engine_data_source_results_stale_reads_the_live_event_log() -> None:
    """The live source flags a post-export correction from events."""
    engine, source = _engine_source_with_correction()
    watermark_at_export = len(engine.events) - 1  # the export predates the edit

    assert source.results_stale(watermark_at_export) is True
    assert source.results_stale(len(engine.events)) is False
    assert source.results_stale(None) is False


def test_empty_data_source_results_stale_is_never_stale() -> None:
    """The empty state (no ride) has nothing published to go stale."""
    source = EmptyDataSource()

    assert source.results_stale(None) is False
    assert source.results_stale(5) is False
