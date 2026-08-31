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
- (d) after the New Ride flow submits, the console switches onto the
  new ride (E9.1.4): the ride-name label shows it, the state is DRAFT,
  and a crossing typed after Start lands on the new ride (a feed row
  appears and an ``audit`` row is written for the new ride's id).

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


def test_new_ride_writes_a_ride_row() -> None:
    """New Ride over an open store persists a ride row (E9.1.2)."""
    result = scenario_runner.run_scenario("new_ride_writes_a_ride_row")

    data = result["data"]
    assert data["ride_count"] == 1, result["context"]
    assert data["ride_names"] == ["Fresh Ride 2026"], result["context"]
    # create_ride writes the stored status; a fresh ride starts DRAFT.
    assert data["ride_statuses"] == ["draft"], result["context"]


def test_record_crossing_appends_an_audit_row() -> None:
    """A recorded crossing persists through Store.append (E9.1.3)."""
    result = scenario_runner.run_scenario("record_crossing_appends_audit_row")

    data = result["data"]
    assert data["has_record_crossing"] is True, result["context"]
    # The staged ride already held a start event; the typed crossing
    # appends after it -- the engine event log, in order.
    assert data["actions"][-1] == "record_crossing", result["context"]


def test_new_ride_switches_console_and_accepts_crossings() -> None:
    """After New Ride, the console runs the new ride (E9.1.4)."""
    result = scenario_runner.run_scenario("new_ride_switches_console_and_accepts_crossings")

    data = result["data"]
    # The console switched onto the new ride: its name is on the label.
    assert data["ride_name_lbl"] == "Fresh Ride 2026", result["context"]
    # A fresh ride is DRAFT: Start enabled, plate entry disabled.
    assert data["status_lbl"] == "DRAFT", result["context"]
    assert data["start_enabled"] is True, result["context"]
    assert data["plate_enabled"] is False, result["context"]
    # A crossing typed after the switch lands on the new ride: a feed
    # row appears and an audit row is written for the new ride.
    assert data["feed_rows"] >= 1, result["context"]
    assert data["feed_plate"] == "12", result["context"]
    assert data["crossings_label"] == "1", result["context"]
    assert data["has_record_crossing"] is True, result["context"]
    assert data["audit_actions"][-1] == "record_crossing", result["context"]
