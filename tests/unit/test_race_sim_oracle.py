# SPDX-License-Identifier: GPL-3.0-only
"""Headless unit tests for tests/acceptance/race_sim_oracle.py.

``race_sim_oracle.py`` is the headless, wx-free test oracle that replays
a saved ``rides.db`` and checks the facts in the race child's
``race-record.json`` envelope plus its four results exports. It is a
plain function library with a ``__main__`` driver; this module
unit-tests the library headless, like every other UI-adjacent *logic*
module in this codebase (R-71) -- no wx, no spawned child, no real
exports. Each test builds a small hand-authored ``rides.db`` in the test
via ``Store.open`` (fresh temp db, so its ``app_session`` insert is
harmless) and drives the oracle's functions over it.

``tests/acceptance/`` carries no ``__init__.py`` (implicit PEP 420
namespace package), so ``race_sim_oracle`` is importable only once its
directory is on ``sys.path`` -- the same insertion
``test_race_child.py`` makes. The import is deferred into a fixture so a
missing ``race_sim_oracle.py`` (this task's RED phase) fails this
module's own tests, never collection of the whole tests/unit session.
"""

from __future__ import annotations

import csv
import json
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

import pytest

from rivercrossing.cards import Shoe
from rivercrossing.ride import Event, RideConfig
from rivercrossing.roster import EntryMode, PlateModel, Roster
from rivercrossing.store import Store

if TYPE_CHECKING:
    from types import ModuleType

_ACCEPTANCE_DIR = Path(__file__).resolve().parents[1] / "acceptance"
if str(_ACCEPTANCE_DIR) not in sys.path:
    sys.path.insert(0, str(_ACCEPTANCE_DIR))


@pytest.fixture(scope="module")
def oracle() -> ModuleType:
    """Return tests/acceptance.race_sim_oracle, imported lazily."""
    import race_sim_oracle  # type: ignore[import-not-found]  # noqa: PLC0415

    return cast("ModuleType", race_sim_oracle)


def _config(*, deck_count: int = 8, jokers_per_deck: int = 2) -> RideConfig:
    """Return the small store ride config the oracle tests use."""
    return RideConfig(
        name="Test Race",
        event_date=date(2026, 9, 20),
        venue="Sea to Sky Gondola",
        lap_km=8.0,
        organizer="GORBA",
        scorer="K. Singh",
        planned_start=datetime(2026, 9, 20, 10, 0),  # noqa: DTZ001 -- naive local, Store's own contract
        planned_duration_s=21600,
        min_lap_s=60,
        entry_mode=EntryMode.MIXED,
        plate_model=PlateModel.RIDER_POOLED,
        deck_count=deck_count,
        jokers_per_deck=jokers_per_deck,
    )


def _roster() -> Roster:
    """Return the two-solo-entry roster the tests persist/read back."""
    roster = Roster(entry_mode=EntryMode.MIXED, plate_model=PlateModel.RIDER_POOLED)
    roster.create_solo_entry(name="Alice", plate="12")
    roster.create_solo_entry(name="Bob", plate="5")
    return roster


def _main_events() -> list[Event]:
    """Return the main fixture's event stream (7 events, 6 deals)."""
    events = [Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})]
    events.extend(
        Event(
            action="record_crossing",
            payload={
                "plate": "12",
                "entry_id": "12",
                "lap": lap,
                "crossed_at": f"2026-09-20T10:{lap:02d}:00",
            },
        )
        for lap in (1, 2, 3)
    )
    events.append(
        Event(
            action="record_crossing",
            payload={"plate": "5", "entry_id": "5", "lap": 1, "crossed_at": "2026-09-20T10:05:00"},
        )
    )
    events.append(Event(action="deal_manual", payload={"plate": "5", "reason": "manual"}))
    events.append(
        Event(
            action="add_crossing_at",
            payload={
                "plate": "5",
                "entry_id": "5",
                "crossed_at": "2026-09-20T10:07:00",
                "reason": "missed",
            },
        )
    )
    return events


def _deal_events() -> list[Event]:
    """Return a stream where every deal backs a crossing."""
    events = [Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})]
    events.extend(
        Event(
            action="record_crossing",
            payload={
                "plate": "12",
                "entry_id": "12",
                "lap": lap,
                "crossed_at": f"2026-09-20T10:{lap:02d}:00",
            },
        )
        for lap in (1, 2)
    )
    events.append(
        Event(
            action="add_crossing_at",
            payload={
                "plate": "5",
                "entry_id": "5",
                "crossed_at": "2026-09-20T10:05:00",
                "reason": "missed",
            },
        )
    )
    return events


def _make_db(tmp_path: Path, events: list[Event]) -> tuple[Path, int]:
    """Build a rides.db from *events*; return ``(db_path, ride_id)``."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(), rng_seed=42)
        store.save_roster(ride_id, _roster())
        for event in events:
            store.append(ride_id, event)
    finally:
        store.close()
    return db_path, ride_id


def _make_reshuffle_db(tmp_path: Path, deals: int) -> tuple[Path, int]:
    """Build a 1-deck, 0-joker ride with *deals* record_crossings."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        ride_id = store.create_ride(_config(deck_count=1, jokers_per_deck=0), rng_seed=42)
        store.save_roster(ride_id, _roster())
        store.append(
            ride_id, Event(action="start", payload={"actual_start": "2026-09-20T10:00:00"})
        )
        for lap in range(1, deals + 1):
            store.append(
                ride_id,
                Event(
                    action="record_crossing",
                    payload={
                        "plate": "12",
                        "entry_id": "12",
                        "lap": lap,
                        "crossed_at": f"2026-09-20T10:{lap:02d}:00",
                    },
                ),
            )
    finally:
        store.close()
    return db_path, ride_id


def _check(report: object, name: str) -> object:
    """Return the Check named *name* from *report*, or fail."""
    for check in report.checks:  # type: ignore[attr-defined]
        if check.name == name:
            return check
    raise AssertionError(f"no check named {name!r}")


def _store_engine(db_path: Path, ride_id: int) -> object:
    """Return Store.load_engine(ride_id) through a read-only Store."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    store = Store(conn)
    try:
        return store.load_engine(ride_id)
    finally:
        store.close()


def _snapshot_cards(engine: object) -> list[tuple[str, tuple[str, ...], int]]:
    """Return ``(plate, card codes, laps)`` per snapshot row."""
    return [
        (row.plate, tuple(card.code() for card in row.cards), row.laps)  # type: ignore[attr-defined]
        for row in engine.snapshot()  # type: ignore[attr-defined]
    ]


def _correct_record(oracle: ModuleType, db_path: Path, ride_id: int) -> dict:
    """Return a self-consistent record for the saved ride."""
    facts = oracle.load_race_facts(db_path)
    engine = oracle.replay(facts, len(facts.events))
    roster = _roster()
    standings = oracle.expected_standings(engine, roster)
    checkpoint = {
        "id": "final",
        "event_count": len(facts.events),
        "status": engine.state.value.upper(),
        "elapsed_s": 3600.0,
        "clock_label": oracle.format_duration(3600.0),
        "feed_rows": [oracle.asdict(row) for row in oracle.expected_feed_rows(engine, roster)],
        "counters": oracle.asdict(oracle.expected_counters(engine, roster)),
        "standings": standings,
    }
    final = [
        {
            "place": row["place"],
            "plate": row["plate"],
            "name": row["entry"],
            "laps": row["laps"],
            "hand": row["hand"],
        }
        for row in standings
    ]
    return {
        "schema_version": 1,
        "ride_id": ride_id,
        "rng_seed": facts.rng_seed,
        "config": {},
        "dealt_cards": oracle.reconstruct_shoe_deals(facts),
        "checkpoints": [checkpoint],
        "final_standings": final,
        "export_paths": {},
        "export_watermark": None,
        "audit_actions": [event.action for event in facts.events],
    }


def test_load_race_facts_reads_config_seed_events_without_session_insert(
    oracle: ModuleType, tmp_path: Path
) -> None:
    """load_race_facts reads facts and never grows the session table."""
    db_path, ride_id = _make_db(tmp_path, _main_events())
    with sqlite3.connect(str(db_path)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM app_session").fetchone()[0]

    facts = oracle.load_race_facts(db_path)

    with sqlite3.connect(str(db_path)) as conn:
        after = conn.execute("SELECT COUNT(*) FROM app_session").fetchone()[0]
    assert after == before
    assert facts.ride_id == ride_id
    assert facts.rng_seed == 42
    assert facts.config.deck_count == 8
    assert facts.config.jokers_per_deck == 2
    assert [event.action for event in facts.events] == [
        "start",
        "record_crossing",
        "record_crossing",
        "record_crossing",
        "record_crossing",
        "deal_manual",
        "add_crossing_at",
    ]


def test_load_race_facts_defaults_to_newest_ride(oracle: ModuleType, tmp_path: Path) -> None:
    """A None ride_id reads the newest ride by id."""
    db_path = tmp_path / "rides.db"
    store = Store.open(db_path)
    try:
        store.create_ride(_config(), rng_seed=11)
        second = store.create_ride(_config(), rng_seed=22)
    finally:
        store.close()

    facts = oracle.load_race_facts(db_path)

    assert facts.ride_id == second
    assert facts.rng_seed == 22


def test_reconstruct_shoe_deals_matches_engine_crossing_cards(
    oracle: ModuleType, tmp_path: Path
) -> None:
    """The standalone shoe walk agrees with the engine's dealt cards."""
    db_path, _ride_id = _make_db(tmp_path, _deal_events())
    facts = oracle.load_race_facts(db_path)
    engine = oracle.replay(facts, len(facts.events))

    engine_cards = [engine.card_for(crossing).code() for crossing in engine.crossings]

    assert oracle.reconstruct_shoe_deals(facts) == engine_cards


def test_reconstruct_shoe_deals_reshuffles_on_empty_shoe(
    oracle: ModuleType, tmp_path: Path
) -> None:
    """A 1-deck, 0-joker shoe reshuffles into cycle 2 after 52 deals."""
    db_path, _ride_id = _make_reshuffle_db(tmp_path, deals=53)
    facts = oracle.load_race_facts(db_path)

    codes = oracle.reconstruct_shoe_deals(facts)

    assert len(codes) == 53
    cycle2 = Shoe(decks=1, jokers_per_deck=0, seed=42 + 1)
    cycle2_first, _index = cycle2.deal()
    assert codes[52] == cycle2_first.code()


def test_replay_reproduces_intermediate_state(oracle: ModuleType, tmp_path: Path) -> None:
    """replay(facts, n) lands on the state the first n events imply."""
    db_path, _ride_id = _make_db(tmp_path, _main_events())
    facts = oracle.load_race_facts(db_path)

    after_start = oracle.replay(facts, 1)
    assert after_start.state.value == "running"
    assert len(after_start.crossings) == 0

    after_three = oracle.replay(facts, 4)
    assert after_three.state.value == "running"
    assert len(after_three.crossings) == 3

    full = oracle.replay(facts, len(facts.events))
    assert full.state.value == "running"
    assert len(full.crossings) == 5


def test_replay_equals_store_load_engine(oracle: ModuleType, tmp_path: Path) -> None:
    """Replay reproduces Store.load_engine's cards and state."""
    db_path, ride_id = _make_db(tmp_path, _main_events())
    facts = oracle.load_race_facts(db_path)

    replayed = oracle.replay(facts, len(facts.events))
    loaded = _store_engine(db_path, ride_id)

    assert replayed.state == loaded.state
    assert _snapshot_cards(replayed) == _snapshot_cards(loaded)


def test_expected_projections_internally_consistent(oracle: ModuleType, tmp_path: Path) -> None:
    """Counters/feed/standings agree with the replayed engine."""
    db_path, _ride_id = _make_db(tmp_path, _main_events())
    facts = oracle.load_race_facts(db_path)
    engine = oracle.replay(facts, len(facts.events))
    roster = _roster()

    counters = oracle.expected_counters(engine, roster)
    assert counters.crossings == len(engine.crossings)
    assert counters.crossings == 5

    feed = oracle.expected_feed_rows(engine, roster)
    assert len(feed) == 5  # under the 30-row feed cap

    standings = oracle.expected_standings(engine, roster)
    places = [row["place"] for row in standings]
    assert places[0] == 1
    assert places == sorted(places)


def test_compare_flags_wrong_dealt_cards(oracle: ModuleType, tmp_path: Path) -> None:
    """A tampered dealt_cards list fails the deal_fidelity check."""
    db_path, ride_id = _make_db(tmp_path, _main_events())
    facts = oracle.load_race_facts(db_path)
    record = _correct_record(oracle, db_path, ride_id)

    assert oracle.compare(facts, record, None).all_pass is True

    tampered = json.loads(json.dumps(record))
    tampered["dealt_cards"] = ["XX", *tampered["dealt_cards"][1:]]
    check = _check(oracle.compare(facts, tampered, None), "deal_fidelity")
    assert check.ok is False
    assert check.detail != ""


def test_compare_flags_wrong_checkpoint_data(oracle: ModuleType, tmp_path: Path) -> None:
    """Tampered feed/counters/standings each fail their own check."""
    db_path, ride_id = _make_db(tmp_path, _main_events())
    facts = oracle.load_race_facts(db_path)
    record = _correct_record(oracle, db_path, ride_id)

    tampered = json.loads(json.dumps(record))
    tampered["checkpoints"][0]["counters"]["crossings"] = 999
    assert _check(oracle.compare(facts, tampered, None), "checkpoint[0].counters").ok is False

    tampered = json.loads(json.dumps(record))
    tampered["checkpoints"][0]["feed_rows"] = []
    assert _check(oracle.compare(facts, tampered, None), "checkpoint[0].feed_rows").ok is False

    tampered = json.loads(json.dumps(record))
    tampered["checkpoints"][0]["standings"][0]["laps"] = 999
    assert _check(oracle.compare(facts, tampered, None), "checkpoint[0].standings").ok is False

    tampered = json.loads(json.dumps(record))
    tampered["final_standings"][0]["laps"] = 999
    assert _check(oracle.compare(facts, tampered, None), "final_standings").ok is False


def test_compare_skips_exports_when_no_dir(oracle: ModuleType, tmp_path: Path) -> None:
    """An exports_dir of None emits no export-named checks."""
    db_path, ride_id = _make_db(tmp_path, _main_events())
    facts = oracle.load_race_facts(db_path)
    record = _correct_record(oracle, db_path, ride_id)

    report = oracle.compare(facts, record, None)

    assert not any(check.name.startswith("export_") for check in report.checks)


def test_render_json_and_markdown_reflect_report(oracle: ModuleType, tmp_path: Path) -> None:
    """render_json/render_markdown round-trip the report's checks."""
    db_path, ride_id = _make_db(tmp_path, _main_events())
    facts = oracle.load_race_facts(db_path)
    report = oracle.compare(facts, _correct_record(oracle, db_path, ride_id), None)

    rendered = oracle.render_json(report)
    assert rendered["all_pass"] is True
    assert [check["name"] for check in rendered["checks"]] == [
        check.name for check in report.checks
    ]

    markdown = oracle.render_markdown(report)
    assert "deal_fidelity" in markdown
    assert "final_standings" in markdown
    assert "ALL CHECKS PASS" in markdown


def test_export_helpers_parse_handwritten_files(oracle: ModuleType, tmp_path: Path) -> None:
    """The HTML/CSV standings readers parse hand-written files."""
    html = tmp_path / "results.html"
    html.write_text(
        '<script type="application/json" id="race-data">'
        '{"results": [{"place": 1, "plate": "12", "laps": 3, "hand": "Trips"}]}'
        "</script>",
        encoding="utf-8",
    )
    assert oracle._html_standings(html) == ((1, "12", 3, "Trips"),)

    csv_path = tmp_path / "standings.csv"
    csv_path.write_text("place,plate,entry,laps,hand\n1,12,Alice,3,Trips\n", encoding="utf-8")
    assert oracle._csv_standings(csv_path) == ((1, "12", 3, "Trips"),)


def test_compare_export_checks_html_and_csv(oracle: ModuleType, tmp_path: Path) -> None:
    """HTML/CSV export checks pass, flag tampering, and gate gaps."""
    db_path, ride_id = _make_db(tmp_path, _main_events())
    facts = oracle.load_race_facts(db_path)
    record = _correct_record(oracle, db_path, ride_id)

    exports_dir = tmp_path / "exports"
    exports_dir.mkdir()
    standings = record["final_standings"]
    html = exports_dir / "results.html"
    results = [
        {"place": row["place"], "plate": row["plate"], "laps": row["laps"], "hand": row["hand"]}
        for row in standings
    ]
    html.write_text(
        '<script type="application/json" id="race-data">'
        + json.dumps({"results": results})
        + "</script>",
        encoding="utf-8",
    )
    csv_path = exports_dir / "standings.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["place", "plate", "entry", "laps", "hand"])
        for row in standings:
            writer.writerow([row["place"], row["plate"], row["name"], row["laps"], row["hand"]])

    record["export_paths"] = {
        "export_html": str(html),
        "export_results_csv": str(csv_path),
    }
    report = oracle.compare(facts, record, exports_dir)
    assert _check(report, "export_html").ok is True
    assert _check(report, "export_csv").ok is True
    assert _check(report, "export_pdf").ok is False  # no path recorded
    assert _check(report, "export_poster").ok is False  # no path recorded

    csv_path.write_text("place,plate,entry,laps,hand\n9,9,Wrong,9,Wrong\n", encoding="utf-8")
    tampered = oracle.compare(facts, record, exports_dir)
    assert _check(tampered, "export_csv").ok is False
    assert _check(tampered, "export_csv").detail != ""


def test_pdf_check_flags_missing_entry(oracle: ModuleType) -> None:
    """The PDF containment check flags a missing entry name."""
    rows = [{"name": "Alice"}, {"name": "Bob"}]
    assert oracle._pdf_check("Full field section\nAlice\nBob", rows).ok is True
    failing = oracle._pdf_check("Full field section\nAlice", rows)
    assert failing.ok is False
    assert "Bob" in failing.detail
