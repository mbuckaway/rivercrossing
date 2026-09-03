# SPDX-License-Identifier: GPL-3.0-only
"""Headless test oracle for the E9.2.1 race record (R-74).

The companion race child (``race_child.py``) writes a
``race-record.json`` envelope and the four results exports into an
artifact directory. This module replays the saved ``rides.db``
independently and checks every fact the record claims -- the
dealt-card sequence, each chronological display checkpoint, the final
standings and the four exports -- without ever touching wx.

It is both a script and a plain function library: the public functions
take a :class:`RaceFacts` plus the parsed record and return an
:class:`AnalysisReport` of named :class:`Check` rows, and the
``__main__`` block drives the whole comparison from one artifact
directory, writing ``analysis-report.md`` and ``analysis.json`` and
exiting nonzero unless every check passes.

Doc-silence resolutions (this module's own):

- **``RaceFacts.db_path``.** :func:`replay` and :func:`compare` rebuild
  a fresh roster per call through ``Store.roster_for``, which needs the
  database. ``RaceFacts`` therefore carries ``db_path`` alongside the
  spec-listed fields so those functions can construct a read-only
  ``Store`` -- never ``Store.open``, whose ``app_session`` insert would
  corrupt the saved database (the project's CRITICAL constraint).
- **PDF check is containment, not tuple equality.** The HTML and CSV
  exports parse to exact ``(place, plate, laps, hand)`` tuples; the PDF
  text is not tabular, so its check asserts the "Full field" section
  plus every entry name, deferring exact ordering to the HTML/CSV
  checks (the same split ``test_full_race_r74.py`` uses).
- **``expected_standings`` accepts an unused ``roster``.** It is kept
  for API symmetry with :func:`expected_feed_rows`; ``RideEngine``.
  ``snapshot`` reads the engine's own roster, so the parameter is never
  consulted.
"""

from __future__ import annotations

import csv
import json
import re
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast

from pypdf import PdfReader

from rivercrossing.cards import Shoe, ShoeEmpty
from rivercrossing.ride import Event, RideEngine
from rivercrossing.standings import hand_name, rank, tiebreak_order_from_spellings
from rivercrossing.store import Store
from rivercrossing.ui.presenters.data_source import EngineDataSource, format_duration

if TYPE_CHECKING:
    from rivercrossing.ride import RideConfig
    from rivercrossing.roster import Roster
    from rivercrossing.ui.presenters.data_source import Counters, FeedRow

__all__ = [
    "AnalysisReport",
    "Check",
    "RaceFacts",
    "compare",
    "expected_counters",
    "expected_feed_rows",
    "expected_standings",
    "load_race_facts",
    "reconstruct_shoe_deals",
    "render_json",
    "render_markdown",
    "replay",
]

# The three engine actions whose replay consumes one shoe card (R-40).
# ``shoe_reshuffle`` is deliberately absent: reconstruction catches
# ``ShoeEmpty`` and calls ``Shoe.reshuffle()`` exactly as the deal loop
# does, never trusting the audit record of the reshuffle.
_DEAL_ACTIONS = ("record_crossing", "deal_manual", "add_crossing_at")


@dataclass(frozen=True, slots=True)
class RaceFacts:
    """One ride's replayable facts: config, seed, and audit events.

    ``events`` holds the ``audit`` rows in insert (id) order -- the
    only order ``Store.load_engine`` replays them. ``db_path`` is
    carried so :func:`replay`/:func:`compare` can rebuild a fresh
    roster through a constructed (read-only) ``Store`` (module
    docstring).
    """

    ride_id: int
    config: RideConfig
    rng_seed: int
    events: tuple[Event, ...]
    db_path: Path


@dataclass(frozen=True, slots=True)
class Check:
    """One named invariant outcome; ``detail`` is a diff on failure."""

    name: str
    ok: bool
    detail: str = ""


@dataclass(frozen=True, slots=True)
class AnalysisReport:
    """Every check :func:`compare` emitted, in order."""

    checks: tuple[Check, ...]

    @property
    def all_pass(self) -> bool:
        """Return True when every check passed."""
        return all(check.ok for check in self.checks)


def _read_store(db_path: Path) -> Store:
    """Return a read-only Store over *db_path* (never ``Store.open``).

    ``Store.open`` inserts an ``app_session`` row (its launch
    bookkeeping); a directly-constructed Store wraps the connection
    without opening, migrating or inserting, so reading through it
    never corrupts the saved database. The connection's ``row_factory``
    is set to ``sqlite3.Row`` because ``Store``'s readers index columns
    by name.
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return Store(conn)


def _roster_for(db_path: Path, ride_id: int) -> Roster:
    """Return a fresh roster for *ride_id* via a constructed Store."""
    store = _read_store(db_path)
    try:
        return store.roster_for(ride_id)
    finally:
        store.close()


def _fixed_clock() -> datetime:
    """Return a pinned naive datetime for stop/finish/reopen re-stamps.

    The clock is only consulted by :meth:`RideEngine.apply` when it
    re-stamps those three clock-derived payload fields; a fixed value
    keeps replay deterministic.
    """
    return datetime(2026, 9, 20, 12, 0)  # noqa: DTZ001 -- naive local, Store's own contract


def load_race_facts(db_path: Path, ride_id: int | None = None) -> RaceFacts:
    """Read one ride's config, seed and audit events from *db_path*.

    The ride row and audit rows are read through a plain ``sqlite3``
    connection; the config is reconstructed through a constructed
    Store's ``load_engine`` so the config-reconstruction logic is never
    duplicated here. ``Store.open`` is never called (module docstring).

    Args:
        db_path: The saved ``rides.db`` file.
        ride_id: The ride to read; ``None`` uses the newest ride.

    Returns:
        The ride's :class:`RaceFacts`.

    Raises:
        ValueError: *db_path* has no ride, or *ride_id* names none.
    """
    with sqlite3.connect(str(db_path)) as conn:
        conn.row_factory = sqlite3.Row
        if ride_id is None:
            newest = conn.execute("SELECT id FROM ride ORDER BY id DESC LIMIT 1").fetchone()
            if newest is None:
                raise ValueError(f"no ride in {db_path}")
            ride_id = int(newest["id"])
        ride_row = conn.execute(
            "SELECT id, rng_seed FROM ride WHERE id = ?", (ride_id,)
        ).fetchone()
        if ride_row is None:
            raise ValueError(f"no ride with id {ride_id} in {db_path}")
        rng_seed = int(ride_row["rng_seed"])
        events = tuple(
            Event(action=audit_row["action"], payload=json.loads(audit_row["payload_json"]))
            for audit_row in conn.execute(
                "SELECT action, payload_json FROM audit WHERE ride_id = ? ORDER BY id",
                (ride_id,),
            )
        )
    store = _read_store(db_path)
    try:
        config = store.load_engine(ride_id).config
    finally:
        store.close()
    return RaceFacts(
        ride_id=ride_id, config=config, rng_seed=rng_seed, events=events, db_path=db_path
    )


def replay(facts: RaceFacts, n: int) -> RideEngine:
    """Replay the first *n* of ``facts.events`` onto a fresh engine.

    A fresh shoe, a fresh roster and a fresh engine are built per call
    so intermediate prefixes never contaminate each other. The clock is
    fixed: it is only used to re-stamp stop/finish/reopen payloads.

    Args:
        facts: The ride to replay.
        n: How many leading events to apply.

    Returns:
        A fresh :class:`RideEngine` in the state after ``n`` events.
    """
    config = facts.config
    shoe = Shoe(
        decks=config.deck_count,
        jokers_per_deck=config.jokers_per_deck,
        seed=facts.rng_seed,
    )
    roster = _roster_for(facts.db_path, facts.ride_id)
    engine = RideEngine(config=config, shoe=shoe, clock=_fixed_clock, roster=roster)
    for event in facts.events[:n]:
        engine.apply(event)
    return engine


def reconstruct_shoe_deals(facts: RaceFacts) -> list[str]:
    """Return every dealt card's code by walking a bare seeded Shoe.

    This is the independent deal-path check: it never touches
    :class:`RideEngine`. A bare ``Shoe`` seeded with ``facts.rng_seed``
    deals once per ``record_crossing``/``deal_manual``/
    ``add_crossing_at`` event, reshuffling on ``ShoeEmpty`` (the
    ``shoe_reshuffle`` audit row is ignored, exactly as the deal loop
    behaves).

    Args:
        facts: The ride whose deal order to reconstruct.

    Returns:
        One card code per deal action, in deal order.
    """
    shoe = Shoe(
        decks=facts.config.deck_count,
        jokers_per_deck=facts.config.jokers_per_deck,
        seed=facts.rng_seed,
    )
    codes: list[str] = []
    for event in facts.events:
        if event.action not in _DEAL_ACTIONS:
            continue
        try:
            card, _index = shoe.deal()
        except ShoeEmpty:
            shoe.reshuffle()
            card, _index = shoe.deal()
        codes.append(card.code())
    return codes


def expected_feed_rows(engine: RideEngine, roster: Roster) -> list[FeedRow]:
    """Return the console feed for *engine*, newest first (cap 30)."""
    return EngineDataSource(engine, roster).feed_rows()


def expected_counters(engine: RideEngine, roster: Roster) -> Counters:
    """Return the console counter chips for *engine*."""
    return EngineDataSource(engine, roster).counters()


def expected_standings(engine: RideEngine, roster: Roster) -> list[dict[str, object]]:
    """Return ranked standings for *engine* as dict rows.

    ``roster`` is accepted for API symmetry with
    :func:`expected_feed_rows` but is never consulted -- ``RideEngine``.
    ``snapshot`` reads the engine's own roster (module docstring).

    Args:
        engine: The replayed engine to rank.
        roster: Unused; kept for signature symmetry.

    Returns:
        One dict per entry, ranked best hand first, with the keys the
        checkpoint record uses (``place``/``plate``/``entry``/``laps``/
        ``total``/``best5``/``hand``/``draw_required``).
    """
    del roster  # signature symmetry; snapshot() reads the engine's own roster
    order = tiebreak_order_from_spellings(engine.config.tiebreak_order)
    rows: list[dict[str, object]] = []
    for placed in rank(engine.snapshot(), order):
        result = placed.result
        try:
            hand = hand_name(result.hand)
        except ValueError:
            hand = ""  # no nameable rank (mirrors EngineDataSource.standings)
        rows.append(
            {
                "place": placed.place,
                "plate": result.plate,
                "entry": result.name,
                "laps": result.laps,
                "total": format_duration(result.total_time),
                "best5": [card.code() for card in result.hand.best5],
                "hand": hand,
                "draw_required": placed.draw_required,
            }
        )
    return rows


def _equal(name: str, actual: object, expected: object) -> Check:
    """Return a Check comparing *actual* to *expected*, with a diff."""
    if actual == expected:
        return Check(name=name, ok=True)
    return Check(name=name, ok=False, detail=f"expected {expected!r}, got {actual!r}")


def _normalize_final(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Project standings rows to the ``final_standings`` keys."""
    return [
        {
            "place": row["place"],
            "plate": row["plate"],
            "name": row["entry"],
            "laps": row["laps"],
            "hand": row["hand"],
        }
        for row in rows
    ]


def compare(
    facts: RaceFacts, record: dict[str, object], exports_dir: Path | None
) -> AnalysisReport:
    """Check every invariant in *record* against *facts*.

    Produces one :class:`Check` per invariant: the dealt-card sequence;
    per checkpoint the feed, counters, status, clock and (when present)
    standings; the final standings; and, when *exports_dir* is given,
    the HTML/CSV/PDF/poster exports.

    Args:
        facts: The ride's facts, from :func:`load_race_facts`.
        record: The parsed ``race-record.json``.
        exports_dir: The directory holding the four exports; ``None``
            skips every export check.

    Returns:
        The named checks in emission order.
    """
    checks: list[Check] = []

    checks.append(
        _equal("deal_fidelity", reconstruct_shoe_deals(facts), record.get("dealt_cards"))
    )

    roster = _roster_for(facts.db_path, facts.ride_id)
    checkpoints = cast("list[dict[str, object]]", record.get("checkpoints", []))
    for index, checkpoint in enumerate(checkpoints):
        label = f"checkpoint[{index}]"
        engine = replay(facts, int(cast("object", checkpoint["event_count"])))

        actual_feed = [asdict(row) for row in expected_feed_rows(engine, roster)]
        checks.append(_equal(f"{label}.feed_rows", actual_feed, checkpoint.get("feed_rows")))

        checks.append(
            _equal(
                f"{label}.counters",
                asdict(expected_counters(engine, roster)),
                checkpoint.get("counters"),
            )
        )

        checks.append(
            _equal(f"{label}.status", engine.state.value.upper(), checkpoint.get("status"))
        )

        elapsed = float(cast("object", checkpoint.get("elapsed_s", 0.0)))
        checks.append(
            _equal(f"{label}.clock", format_duration(elapsed), checkpoint.get("clock_label"))
        )

        recorded_standings = checkpoint.get("standings")
        if recorded_standings is not None:
            checks.append(
                _equal(
                    f"{label}.standings", expected_standings(engine, roster), recorded_standings
                )
            )

    final_engine = replay(facts, len(facts.events))
    final_rows = _normalize_final(expected_standings(final_engine, roster))
    checks.append(_equal("final_standings", final_rows, record.get("final_standings")))

    if exports_dir is not None:
        checks.extend(_export_checks(record, exports_dir, final_rows))

    return AnalysisReport(checks=tuple(checks))


def _resolve_export(raw_paths: dict[str, object], exports_dir: Path, target: str) -> Path | None:
    """Return *target*'s resolved export path, or None if unrecorded."""
    raw = raw_paths.get(target)
    if raw is None:
        return None
    path = Path(str(raw))
    return path if path.is_absolute() else exports_dir / path


def _standings_file(name: str, path: Path | None) -> Check | None:
    """Return a failing Check when *path* is unusable, else None."""
    if path is None:
        return Check(name=name, ok=False, detail="export path not recorded")
    if not path.exists():
        return Check(name=name, ok=False, detail=f"missing export file: {path}")
    if path.stat().st_size == 0:
        return Check(name=name, ok=False, detail=f"empty export file: {path}")
    return None


def _pdf_check(text: str, final_rows: list[dict[str, object]]) -> Check:
    """Return the PDF check: full field plus every entry name."""
    missing: list[str] = []
    if "Full field" not in text:
        missing.append("Full field section")
    for row in final_rows:
        name = str(row["name"])
        if name not in text:
            missing.append(f"entry {name!r}")
    if missing:
        return Check(name="export_pdf", ok=False, detail="missing: " + ", ".join(missing))
    return Check(name="export_pdf", ok=True)


def _export_checks(
    record: dict[str, object], exports_dir: Path, final_rows: list[dict[str, object]]
) -> list[Check]:
    """Return the four export checks for a recorded, exported race."""
    projected = tuple(
        (int(row["place"]), str(row["plate"]), int(row["laps"]), str(row["hand"]))
        for row in final_rows
    )
    raw_paths = cast("dict[str, object]", record.get("export_paths") or {})
    checks: list[Check] = []

    html_path = _resolve_export(raw_paths, exports_dir, "export_html")
    gate = _standings_file("export_html", html_path)
    if gate is not None:
        checks.append(gate)
    else:
        try:
            actual = _html_standings(html_path)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 -- an export parse failure is a failing check, not a crash
            checks.append(Check(name="export_html", ok=False, detail=f"parse error: {exc}"))
        else:
            checks.append(_equal("export_html", actual, projected))

    csv_path = _resolve_export(raw_paths, exports_dir, "export_results_csv")
    gate = _standings_file("export_csv", csv_path)
    if gate is not None:
        checks.append(gate)
    else:
        try:
            actual = _csv_standings(csv_path)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(name="export_csv", ok=False, detail=f"parse error: {exc}"))
        else:
            checks.append(_equal("export_csv", actual, projected))

    pdf_path = _resolve_export(raw_paths, exports_dir, "export_pdf")
    gate = _standings_file("export_pdf", pdf_path)
    if gate is not None:
        checks.append(gate)
    else:
        try:
            text = _pdf_text(pdf_path)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001
            checks.append(Check(name="export_pdf", ok=False, detail=f"parse error: {exc}"))
        else:
            checks.append(_pdf_check(text, final_rows))

    poster_path = _resolve_export(raw_paths, exports_dir, "export_poster")
    gate = _standings_file("export_poster", poster_path)
    checks.append(gate if gate is not None else Check(name="export_poster", ok=True))

    return checks


# ---------------- export readers (mirror test_full_race_r74.py)

_RACE_DATA_RE = re.compile(
    r'<script type="application/json" id="race-data">(.*?)</script>', re.DOTALL
)


def _race_data_block(path: Path) -> dict[str, object]:
    """Extract and parse a results page's ``race-data`` JSON block.

    Raises:
        ValueError: *path* has no ``race-data`` block.
    """
    html = path.read_text(encoding="utf-8")
    match = _RACE_DATA_RE.search(html)
    if match is None:
        raise ValueError(f"no race-data block found in {path}")
    return cast("dict[str, object]", json.loads(match.group(1)))


def _html_standings(path: Path) -> tuple[tuple[int, str, int, str], ...]:
    """Read the HTML race-data results as (place, plate, laps, hand)."""
    record = _race_data_block(path)
    results = cast("list[dict[str, object]]", record["results"])
    return tuple(
        (int(row["place"]), str(row["plate"]), int(row["laps"]), str(row["hand"]))
        for row in results
    )


def _csv_standings(path: Path) -> tuple[tuple[int, str, int, str], ...]:
    """Read the standings CSV rows as ``(place, plate, laps, hand)``.

    Raises:
        ValueError: *path*'s header row is not the standings header.
    """
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.reader(handle))
    if not rows or rows[0] != ["place", "plate", "entry", "laps", "hand"]:
        raise ValueError(f"unexpected standings CSV header in {path}")
    return tuple((int(row[0]), row[1], int(row[3]), row[4]) for row in rows[1:])


def _pdf_text(path: Path) -> str:
    """Return every page's extracted text, joined with newlines."""
    reader = PdfReader(str(path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def render_json(report: AnalysisReport) -> dict[str, object]:
    """Return *report* as a JSON-ready dict."""
    return {
        "all_pass": report.all_pass,
        "checks": [
            {"name": check.name, "ok": check.ok, "detail": check.detail} for check in report.checks
        ],
    }


def render_markdown(report: AnalysisReport) -> str:
    """Return *report* as a Markdown analysis document."""
    lines = ["# Race simulation analysis", ""]
    for check in report.checks:
        verdict = "PASS" if check.ok else "FAIL"
        lines.append(f"- **[{verdict}]** `{check.name}`")
        if check.detail:
            lines.append(f"  - {check.detail}")
    lines.append("")
    result = "ALL CHECKS PASS" if report.all_pass else "FAILURES PRESENT"
    lines.append(f"**Result:** {result}")
    return "\n".join(lines)


def _main(argv: list[str]) -> int:
    """Run the oracle over one artifact directory; return the exit code.

    Loads ``<artifact_dir>/race-record.json`` and ``rides.db``, compares
    them (skipping export checks when ``<artifact_dir>/exports`` is
    absent), and writes ``analysis-report.md`` and ``analysis.json``.
    A load/compare failure is reported as a single failing check rather
    than a traceback.
    """
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <artifact_dir>", file=sys.stderr)  # noqa: T201
        return 2
    artifact_dir = Path(argv[1])
    exports_dir = artifact_dir / "exports"
    try:
        record = json.loads((artifact_dir / "race-record.json").read_text(encoding="utf-8"))
        facts = load_race_facts(artifact_dir / "rides.db")
        report = compare(facts, record, exports_dir if exports_dir.is_dir() else None)
    except Exception as exc:  # noqa: BLE001 -- a run failure is reported as one failing check
        report = AnalysisReport(
            checks=(Check(name="oracle", ok=False, detail=f"{type(exc).__name__}: {exc}"),)
        )
    (artifact_dir / "analysis.json").write_text(
        json.dumps(render_json(report), indent=2) + "\n", encoding="utf-8"
    )
    (artifact_dir / "analysis-report.md").write_text(
        render_markdown(report) + "\n", encoding="utf-8"
    )
    return 0 if report.all_pass else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
