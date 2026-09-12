# SPDX-License-Identifier: GPL-3.0-only
"""Shared module-level helpers for the split list-window suites.

The list-window functional suites (entry-detail, results) spread their
per-worker window churn across ``--dist loadfile`` workers (the
wrapper-cache corruption remedy). The constants and helpers every one
of the files needs live here, under a name pytest never collects; each
file keeps its own private helpers (the per-window ``_StubSource``
classes, the module-scoped ``shared_*`` fixtures) inline.
"""

from typing import Any
from unittest.mock import MagicMock

from rivercrossing.roster import EntryMode, PlateModel, Rider, Roster
from rivercrossing.ui import ids

MAX_SCREEN_WIDTH = 1366
MAX_SCREEN_HEIGHT = 768

# --- xrc-windows.md's own tables, transcribed independently of the
# canvas so a transcription mistake in either place is caught by the
# other disagreeing, not by this test checking itself against itself. ---
CANVAS_LAPS_CARD_KEYS = ("Kc", "joker")  # KC -> Kc, JK -> joker (asset_key)
CANVAS_CARDS_HELD_KEYS = ("9h", "Ks", "Kc", "joker", "4d")  # the 5-of-9 fixture

CANVAS_PUBLISH_DEFAULTS = (
    (ids.SHOW_TIMES_CHK, False),
    (ids.LAPS_BOARD_CHK, True),
    (ids.TIME_BOARD_CHK, False),
    (ids.FULL_FIELD_CHK, True),
    (ids.ALL_CARDS_CHK, True),
)


def _model_row(model: Any, row: int, columns: range) -> tuple[str, ...]:  # noqa: ANN401
    """Return every text cell of *row*, in column order."""
    return tuple(model.GetValueByRow(row, col) for col in columns)


def demo_seeded_roster() -> Roster:
    """Build the mixed, rider_pooled seeded roster the list suites use.

    Test-only fixture: two solo entries ("123" Sam Ellis, "212" M. Chen)
    and one team ("Trail Blazers": A. Roy "77", K. Singh "78"), seeded
    like a store-backed ride so the team carries a deterministic logo
    card (the teams editor's suite reads it off the seeded sequence).
    """
    roster = Roster(
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        max_team_size=4,
        team_logo_seed=8843,
    )
    roster.create_solo_entry(first_name="Sam Ellis", last_name="", plate="123")
    roster.create_team_entry(
        display_name="Trail Blazers",
        riders=[
            Rider(first_name="A. Roy", last_name="", plate="77"),
            Rider(first_name="K. Singh", last_name="", plate="78"),
        ],
    )
    roster.create_solo_entry(first_name="M. Chen", last_name="", plate="212")
    return roster


def _spy_repaint(control: Any) -> tuple[MagicMock, MagicMock]:  # noqa: ANN401
    """Replace *control*'s Refresh/Update with spies; return both.

    Monkeypatching a real wx control's bound methods is a
    platform/GUI I/O boundary (T-10), the same category
    ``test_dialog_behavior.py``'s own ``_spy_on_set_focus`` already
    treats as legitimate to spy on directly in this codebase.

    *control* must stay referenced by a local in the caller for as
    long as the spy needs to see calls: measured (a throwaway probe
    script, per this repo's convention), wxPython's wrapper cache is
    weak, and a ``FindWindowByName`` result with no other surviving
    Python reference is collected -- the *next* lookup of the same
    control then builds a brand-new wrapper, missing this one's
    instance attributes entirely.
    """
    refresh, update = MagicMock(), MagicMock()
    control.Refresh = refresh
    control.Update = update
    return refresh, update
