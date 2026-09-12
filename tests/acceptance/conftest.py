# SPDX-License-Identifier: GPL-3.0-only
"""Refuse to run the acceptance suite.

These tests drive the real wx UI in child processes and take over the
desktop. They must never be run from a developer machine; the
``tests/functional`` conftest refuses its own suite the same way. Exit
at collection -- printing ``DO NOT RUN. WE ARE BROKEN`` -- so any
pytest invocation that reaches this directory dies before a single
test body, window, or control.

The helper modules (``race_child``, ``race_sim_oracle``) stay
importable: the unit tests cover their pure logic headlessly.
"""

import pytest

pytest.exit("DO NOT RUN. WE ARE BROKEN", returncode=1)
