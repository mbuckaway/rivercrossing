# SPDX-License-Identifier: GPL-3.0-only
"""Real-wx tests for EPIC 9 Phase 1: the store-backed app write path.

The store module is complete and unit-tested, but the production UI
had no write path to it: ``main()`` passed ``store=None`` into
``build_main_window``, no production code called ``Store.create_ride``/
``Store.save_roster``/``Store.append``, and the setup presenter
persisted nothing. These scenarios prove the three wire-ups end to
end against a real ``rides.db``:

- (a) ``main()``'s own bootstrap resolves the db path, opens the
  Store, and threads it into the window -- so a staged crash at the
  previous exit fires ``resume_dlg`` with the crash wording (R-52)
  with no test-supplied store.
- (b) the New Ride flow persists a ``ride`` row (and its roster)
  when a store is open.
- (c) every recorded crossing appends an ``audit`` row via
  ``Store.append`` -- not just plate entry, but every engine event.

Each scenario mutates process-global state (the bootstrap modal, the
live console engine, the quit flag), so each runs in a fresh, spawned
interpreter via ``console_subprocess_scenarios.py`` (its own module
docstring records the address-reuse and modal-hang reasons that force
the subprocess pattern).
"""

import pytest
import scenario_runner

pytestmark = pytest.mark.functional


def test_bootstrap_opens_the_store_and_resume_dlg_shows_crash_wording() -> None:
    """main()'s own launch against a staged crash shows resume_dlg."""
    result = scenario_runner.run_scenario("bootstrap_main_launch_resumes_staged_running_ride")

    data = result["data"]
    assert data["resume_dlg_shown"] is True, result["context"]
    assert data["store_open"] is True, result["context"]
    # The copy is never blank and names the ride (task-brief E5.2.2).
    assert data["message_lbl"] != "", result["context"]
    assert "GORBA EPIC 2026" in data["message_lbl"], result["context"]
    # spec §3's crash wording, from the pinned last heartbeat.
    assert "closed unexpectedly at 12:37" in data["message_lbl"], result["context"]
