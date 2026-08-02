# SPDX-License-Identifier: GPL-3.0-only
"""Mypy fixture: a ``ConsoleView`` missing its ``play`` method.

Static-typing-only fixture for
``test_protocols.py::test_console_view_missing_method_fails_mypy_typecheck``
(E1.2.3's mandated "mypy snapshot test", T-5/negative-path sibling for
the type system rather than runtime). ``IncompleteConsoleView``
implements every ``ConsoleView`` member except ``play``; passing it to
a function typed to require a full ``ConsoleView`` must fail
``mypy --strict``, naming ``play`` as the missing member. This file is
never imported at runtime by anything except the mypy subprocess the
test spawns.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rivercrossing.ride import RideStatus
    from rivercrossing.ui.presenters.console import ConsoleView
    from rivercrossing.ui.presenters.data_source import Counters, FeedRow


class IncompleteConsoleView:
    """Every ``ConsoleView`` member except ``play``."""

    def show_feed(self, rows: list[FeedRow]) -> None:
        """No-op fixture stub."""

    def show_counters(self, c: Counters) -> None:
        """No-op fixture stub."""

    def flash_crossing(self, r: FeedRow) -> None:
        """No-op fixture stub."""

    def set_state(self, status: RideStatus) -> None:
        """No-op fixture stub."""

    def focus_entry(self) -> None:
        """No-op fixture stub."""


def _accepts_console_view(view: ConsoleView) -> None:
    """Type-check-only sink requiring a complete ``ConsoleView``."""


_accepts_console_view(IncompleteConsoleView())
