# SPDX-License-Identifier: GPL-3.0-only
"""Real-widget state-enablement check for the console (E1.4.2, R-36).

The full item x state enablement matrix, every named ``Enablement``
condition's boundary/negative cases, and ``is_stop_button_enabled``
(R-35) are pure Python against ``commands.py`` -- no ``wx`` involved
-- so they live in ``tests/unit/ui/test_commands.py`` instead
(mirroring the split ``cards_imagelist`` already uses). What stays
here is the one fact only a real, loaded ``main_frame`` can supply:
that the console's ``arm_stop_chk`` checkbox is genuinely unticked by
default in the authored XRC, which is R-35's precondition.
"""

import harness
import pytest

from rivercrossing.ui import commands, ids

pytestmark = pytest.mark.functional


def test_stop_button_starts_disarmed_in_the_authored_xrc(xrc_resource: object) -> None:
    """R-35: the console's Arm checkbox is unticked by default (§3)."""
    frame = harness.load_window(xrc_resource, ids.MAIN_FRAME, frame=True)

    try:
        armed = harness.find_control(frame, ids.ARM_STOP_CHK).GetValue()
    finally:
        harness.close_window(frame)

    assert commands.is_stop_button_enabled(armed=armed) is False
