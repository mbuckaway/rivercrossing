# SPDX-License-Identifier: GPL-3.0-only
"""Headless tests for app.py's E7.2.1 correction route dispatch.

The six Cards/Riders correction rows dispatch through
``_make_route_handler`` by their own item id -- not by target, since
``mi_add_crossing_at`` and ``mi_edit_crossing`` share
``EDIT_CROSSING_DLG`` with different modes. This pins that dispatch
headlessly with stub handlers; the handlers themselves (real wx
dialogs + real engine commands) are functionally covered by
``tests/functional/test_corrections.py`` and
``test_void_card_confirm.py``.
"""

import pytest

from rivercrossing.ui import app as app_module
from rivercrossing.ui import commands, ids

_CORRECTION_ROUTES = (
    ids.MI_ADD_CROSSING_AT,
    ids.MI_EDIT_CROSSING,
    ids.MI_REASSIGN_PLATE,
    ids.MI_DEAL_MANUAL,
    ids.MI_MARK_DNF,
    ids.MI_VOID_CARD,
)


class _StubContext:
    """A minimal context the stub handlers record."""


@pytest.mark.parametrize("item_id", _CORRECTION_ROUTES, ids=lambda value: value)
def test_make_route_handler_dispatches_each_correction_route_by_its_own_id(
    monkeypatch: pytest.MonkeyPatch, item_id: str
) -> None:
    """Each correction item id binds to its own handler."""
    route = commands.route_for_id(item_id)
    fired: list[object] = []

    def handler(context: object) -> None:
        fired.append(context)

    monkeypatch.setitem(app_module._CORRECTION_HANDLERS, item_id, handler)
    context = _StubContext()
    bound = app_module._make_route_handler(context, route)  # type: ignore[arg-type]

    bound(None)

    assert fired == [context]


def test_shared_edit_crossing_target_dispatch_differs_by_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Add and Edit share a target but bind different handlers."""
    assert commands.route_for_id(ids.MI_ADD_CROSSING_AT).target == ids.EDIT_CROSSING_DLG
    assert commands.route_for_id(ids.MI_EDIT_CROSSING).target == ids.EDIT_CROSSING_DLG
    fired: list[str] = []
    monkeypatch.setitem(
        app_module._CORRECTION_HANDLERS,
        ids.MI_ADD_CROSSING_AT,
        lambda _context: fired.append("add"),
    )
    monkeypatch.setitem(
        app_module._CORRECTION_HANDLERS,
        ids.MI_EDIT_CROSSING,
        lambda _context: fired.append("edit"),
    )
    context = _StubContext()

    app_module._make_route_handler(context, commands.route_for_id(ids.MI_ADD_CROSSING_AT))(None)  # type: ignore[arg-type]
    app_module._make_route_handler(context, commands.route_for_id(ids.MI_EDIT_CROSSING))(None)  # type: ignore[arg-type]

    assert fired == ["add", "edit"]


def test_make_route_handler_leaves_non_correction_dialogs_to_open_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A DIALOG row outside the correction set opens generically."""
    route = commands.route_for_id("mi_set_start_time")
    opened: list[object] = []
    monkeypatch.setattr(app_module, "_open_target", lambda _context, _route: opened.append(_route))
    context = _StubContext()

    bound = app_module._make_route_handler(context, route)  # type: ignore[arg-type]
    bound(None)

    assert opened == [route]
