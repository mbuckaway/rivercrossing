# SPDX-License-Identifier: GPL-3.0-only
"""Tests for the shared view helpers (``ui.views._support``).

``_find`` and the card-imagelist cache this module now hosts are
already exercised end to end by every other view's own tests --
each view still calls them through its own thin ``_find`` method or
``card_images`` attribute, so a construction failure there would
show up as a failure in ``test_console_demo.py``/``test_lists_demo.
py``, not silently. :func:`associate_model` is new behaviour
(unverified repaint remedy, see its own docstring); this module
pins its exact contract in isolation, with no window required.
"""

from unittest.mock import MagicMock, call

import pytest

from rivercrossing.ui.views import _support

pytestmark = pytest.mark.functional


def test_associate_model_associates_then_refreshes_then_updates() -> None:
    """The exact order this unverified repaint remedy depends on."""
    control = MagicMock()
    model = MagicMock()

    _support.associate_model(control, model)

    assert control.mock_calls == [call.AssociateModel(model), call.Refresh(), call.Update()]
