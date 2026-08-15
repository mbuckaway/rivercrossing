# SPDX-License-Identifier: GPL-3.0-only
"""Shared module-level helpers for the split list-window suites.

``test_lists_demo.py`` was split into three files (demo, entry-detail,
results) so the two heaviest functional files spread their per-worker
window churn across ``--dist loadfile`` workers (the wrapper-cache
corruption remedy). The constants and helpers every one of the three
files needs live here, under a name pytest never collects; each file
keeps its own private helpers (the per-window ``_StubSource`` classes,
the module-scoped ``shared_*`` fixtures) inline.
"""

from typing import Any
from unittest.mock import MagicMock

from rivercrossing.ui import ids

MAX_SCREEN_WIDTH = 1366
MAX_SCREEN_HEIGHT = 768

# --- xrc-windows.md's own tables, transcribed independently of demo.py
# so a transcription mistake in either place is caught by the other
# disagreeing, not by this test checking demo.py against itself. ---

CANVAS_RIDES = (
    ("GORBA EPIC 2026", "2026-09-20", "RUNNING", "180"),
    ("Club poker night", "2026-06-11", "FINISHED", "24"),
)

CANVAS_RIDERS = (
    ("123", "Sam Ellis", "—"),
    ("77", "A. Roy", "Trail Blazers"),
    ("78", "K. Singh", "Trail Blazers"),
    ("212", "M. Chen", "—"),
)

CANVAS_ENTRY_HEADER = "Team · 3 riders · 9 laps · 3:02:11"
CANVAS_ENTRY_MEMBERS = "A. Roy (77) · K. Singh (78) · L. Marchetti (79)"
CANVAS_LAPS = (
    ("9", "14:22:18", "19:55", "78"),
    ("8", "14:02:23", "21:40", "77"),
)
CANVAS_LAPS_CARD_KEYS = ("Kc", "joker")  # KC -> Kc, JK -> joker (asset_key)
CANVAS_CARDS_HELD_KEYS = ("9h", "Ks", "Kc", "joker", "4d")  # demo.py's own 5-of-9 fixture

CANVAS_STANDINGS = (
    ("1", "77", "Trail Blazers", "9", "5:44:02", "K♠ K♣ K♦ JK★ 9♥", "Four of a kind, kings"),
    ("2", "123", "Sam Ellis", "8", "5:51:17", "Q♥ J♥ T♥ 9♥ 8♥", "Straight flush, queen-high"),
    ("3", "8", "R. Dubois", "7", "5:38:44", "A♣ A♦ A♥ 4♦ 4♠", "Full house, aces over fours"),
)

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
