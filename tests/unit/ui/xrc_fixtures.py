# SPDX-License-Identifier: GPL-3.0-only
"""Shared XRC arrange step for the degraded-load (Fault-B) tests.

``ui.views._support.load_dialog``/``load_menubar`` retry a miss against
``_support.fresh_resource``, so a target genuinely no ``.xrc`` authors
misses twice. A headless test must therefore pin the second attempt too,
or the retry would build a real ``XmlResource`` mid-suite.

Three test modules had their own copy of that pin (``test_app_logging``,
``test_crossing_detail``, ``test_rider_issues_view``), each with its own
resource double. This module holds the one shared arrange step; each
caller passes the factory for its own absent-resource double.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from rivercrossing.ui.views import _support

if TYPE_CHECKING:
    from collections.abc import Callable

    import pytest


def pin_no_authored_window(
    monkeypatch: pytest.MonkeyPatch,
    absent_resource: Callable[[], object],
) -> None:
    """Pin the self-heal's rebuild to *absent_resource* (the D1 miss).

    *absent_resource* returns a resource double whose every load reports
    no authored window, so both the primary load and the self-heal's
    retry miss. Pinning the retry also keeps a real ``XmlResource`` out
    of a headless test.

    Args:
        monkeypatch: The test's monkeypatch fixture.
        absent_resource: Factory for the doubles that answer the retry.
    """
    monkeypatch.setattr(_support, "fresh_resource", absent_resource)
