# SPDX-License-Identifier: GPL-3.0-only
"""Shared fixture paths and builders for the PDF export tests (P7).

Both ``test_pdfexport.py`` and ``tools/gen_pdfexport_fixtures.py``
build the PDF report from the same inputs: a ride-like stub, a
deterministic field of placed standings drawn from a seeded shoe, the
all-features ``ExportOptions``, and one pinned aware-UTC creation
stamp (D14). Sharing the builder is what makes "regenerate the golden
with the real renderer, then prove the committed bytes match" honest:
the test and the generator cannot drift apart on the inputs.

* ``tests/unit/fixtures/pdfexport/epic-2026-results.pdf`` -- the
  frozen GOLDEN report, regenerated once by
  ``tools/gen_pdfexport_fixtures.py`` from the real renderer;
  ``test_pdfexport.py`` compares ``render(...)`` output to it
  byte-for-byte (R-62).
* ``tests/unit/fixtures/pdfexport/epic-2026-podium.pdf`` -- the frozen
  GOLDEN podium poster (P8, E6.3.2), regenerated once by the same
  generator from ``podium_poster(...)``; the poster tests compare its
  output byte-for-byte.
"""

from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from rivercrossing.cards import Shoe
from rivercrossing.hands import best_hand
from rivercrossing.htmlexport import ExportOptions
from rivercrossing.standings import EntryResult, Placed, rank_by_kind

_ROOT = Path(__file__).resolve().parents[2]
FIXTURES_DIR = _ROOT / "tests" / "unit" / "fixtures" / "pdfexport"
GOLDEN_PDF = FIXTURES_DIR / "epic-2026-results.pdf"
GOLDEN_POSTER = FIXTURES_DIR / "epic-2026-podium.pdf"

# The one timestamp a render embeds: pinned as tz-aware UTC (D14) so
# /CreationDate never bakes a machine-local offset. Identical inputs
# plus this stamp produce byte-identical output (R-62); the golden is
# frozen with it.
FIXED_CREATED = datetime(
    2026,
    9,
    20,
    20,
    7,
    0,
    tzinfo=timezone.utc,  # noqa: UP017 -- this mypy build lacks datetime.UTC; portable
)

# The seeded shoe's deal order is the dataset's only source of
# entropy; a different seed is a deliberate regeneration, the same
# honesty rule gen_rank_vectors.py applies to its sweep.
_GOLDEN_SEED = 20260920


@dataclass(frozen=True, slots=True)
class StubRide:
    """The render() seam: a ride-like object with the read fields.

    ``RideConfig`` satisfies the same Protocol structurally; the stub
    lets the tests and the golden generator avoid a heavy ride
    fixture (the htmlexport D15 seam's own pattern).
    """

    name: str
    event_date: date
    venue: str
    lap_km: float
    organizer: str
    scorer: str


def build_ride() -> StubRide:
    """Return the golden report's ride, the [5a] sample's event."""
    return StubRide(
        name="GORBA EPIC & MTB Festival 2026",
        event_date=date(2026, 9, 20),
        venue="Guelph Lake MTB Trails",
        lap_km=8.0,
        organizer="GORBA — J. Marsden",
        scorer="D. Whitfield",
    )


def build_placed(count: int = 50) -> tuple[Placed, ...]:
    """Rank *count* deterministic entries into Phase 3's two sections.

    Each entry deals 6..8 cards and records 4..10 laps; the last entry
    is a DNF. All values follow from the seed, so the golden is
    reproducible -- and the totals the cover block shows are exact
    (50 entries, 347 laps, 349 cards for the default *count*).

    The mixed field (every third entry is a team) is ranked through
    ``rank_by_kind`` and merged Teams-then-Solo -- the exact sequence
    the app hands the exporters, so the golden's full field shows the
    two per-kind sections with per-kind places.
    """
    shoe = Shoe(decks=8, jokers_per_deck=2, seed=_GOLDEN_SEED)
    results: list[EntryResult] = []
    for index in range(count):
        plate = str(index + 1)
        laps = 4 + (index % 7)
        cards = tuple(shoe.deal()[0] for _ in range(6 + (index % 3)))
        results.append(
            EntryResult(
                entry_id=plate,
                plate=plate,
                name=f"Entry {index + 1}",
                kind="team" if index % 3 == 0 else "solo",
                laps=laps,
                total_time=float(laps * 1800 + (index % 11) * 60),
                best_lap=1500.0 + float(index % 9) * 20.0,
                cards=cards,
                hand=best_hand(cards),
                dnf=(index == count - 1),
            )
        )
    teams, solo = rank_by_kind(results)
    return (*teams, *solo)


def golden_opts() -> ExportOptions:
    """Every section and flag on, so the golden exercises them all."""
    return ExportOptions(
        show_times=True,
        laps_board=True,
        time_board=True,
        full_field=True,
        all_cards=True,
    )
