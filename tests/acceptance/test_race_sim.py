# SPDX-License-Identifier: GPL-3.0-only
"""Full-breadth scripted race acceptance: child record vs. oracle.

The E9.2.2 full-breadth race: the parent stages a rich six-entry
roster ride on a fresh ``rides.db``, spawns ``race_child.py sim_race``
-- which opens the real store-backed app on an injected clock and
drives every correction path (short-lap holds, manual deal, DNF,
add-crossing-at-time, void-crossing, void-card) across two
finish/reopen legs before exporting the four results files -- and then
replays the saved database independently through ``race_sim_oracle``
to check every fact the child's ``race-record.json`` journal claims.

The parent never opens a ``Store`` itself (``Store.open`` inserts an
``app_session`` row and would corrupt the session sequence under test
-- the same rule ``test_full_race_r74.py`` documents); the ride is
staged through ``store_staging`` and every read goes through the
oracle's read-only ``load_race_facts``. The exports are verified by
the oracle parsing the real files, plus a direct existence assertion
here.

Only runnable where real wx windows can open (the Tart VM); on a bare
host the child cannot construct ``main_frame``. ``tests/functional/``
and ``tests/acceptance/`` carry no ``__init__.py``, so
``scenario_runner`` / ``store_staging`` are importable only once
``tests/functional`` is on ``sys.path`` (the same insertion
``test_full_race_r74.py`` makes), and ``race_sim_oracle`` resolves
from this module's own directory.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
import race_sim_oracle

if TYPE_CHECKING:
    from types import ModuleType

_FUNCTIONAL_DIR = Path(__file__).resolve().parents[1] / "functional"
if str(_FUNCTIONAL_DIR) not in sys.path:
    sys.path.insert(0, str(_FUNCTIONAL_DIR))

pytestmark = pytest.mark.functional

# The child's public env contract (race_child.py module docstring),
# hardcoded here so this module stays importable in the RED phase; a
# drift surfaces as a child failure, never a silent pass.
RACE_DB_ENV = "RIVERCROSSING_RACE_DB"
RACE_OUTPUT_DIR_ENV = "RIVERCROSSING_RACE_OUTPUT_DIR"
RACE_EXPORTS_DIR_ENV = "RIVERCROSSING_RACE_EXPORTS_DIR"
RACE_BOUND_ENV = "RIVERCROSSING_RACE_BOUND_SECONDS"
RACE_CHILD = Path(__file__).resolve().parent / "race_child.py"

# The race's pinned shoe seed (E9.2.2/R-77): the nightly owns it and
# injects it here, filing it on failure; RIVERCROSSING_ACCEPTANCE_SEED
# overrides it (the same override ``test_full_race_r74.py`` honours).
_SIM_SEED = 9_001_001

# The child self-terminates (os._exit 124) after its own bound; it quits
# on its own in a few seconds, so 120 s is generous and sits below the
# parent's 150 s timeout (the same ordering test_full_race_r74.py uses).
_CHILD_BOUND_S = 120
_RUN_CHILD_TIMEOUT_S = 150.0

_RACE_ENV_NAMES = (
    RACE_DB_ENV,
    RACE_OUTPUT_DIR_ENV,
    RACE_EXPORTS_DIR_ENV,
    RACE_BOUND_ENV,
)


@pytest.fixture(scope="module")
def race_support() -> ModuleType:
    """Return a namespace of the race-test support modules, lazily."""
    import scenario_runner  # type: ignore[import-not-found]  # noqa: PLC0415
    import store_staging  # type: ignore[import-not-found]  # noqa: PLC0415

    return cast(
        "ModuleType",
        SimpleNamespace(scenario_runner=scenario_runner, store_staging=store_staging),
    )


def _run_sim_race_child(  # noqa: PLR0913 -- the spawn inputs (support, db, output, exports, timeout)
    support: Any,  # noqa: ANN401 -- the lazily-imported module namespace
    *,
    db_path: Path,
    output_dir: Path,
    exports_dir: Path,
    timeout: float = _RUN_CHILD_TIMEOUT_S,
) -> dict[str, Any]:
    """Spawn the ``sim_race`` child; decode its JSON envelope.

    The env vars are set on ``os.environ`` around the spawn (Popen
    inherits it) and restored after, so ``scenario_runner._run_bounded``
    itself stays untouched (the same technique ``test_full_race_r74``'s
    ``_run_child`` uses).
    """
    saved = {name: os.environ.get(name) for name in _RACE_ENV_NAMES}
    os.environ[RACE_DB_ENV] = str(db_path)
    os.environ[RACE_OUTPUT_DIR_ENV] = str(output_dir)
    os.environ[RACE_EXPORTS_DIR_ENV] = str(exports_dir)
    os.environ[RACE_BOUND_ENV] = str(_CHILD_BOUND_S)
    try:
        completed = support.scenario_runner._run_bounded(
            [sys.executable, str(RACE_CHILD), "sim_race"], timeout
        )
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return support.scenario_runner._decode_scenario_output("sim_race", completed)


def test_race_sim_full_breadth_race_matches_the_algorithm(
    race_support: ModuleType, tmp_path: Path
) -> None:
    """The child's full-breadth record matches the oracle's replay."""
    support = cast("Any", race_support)
    store_staging = support.store_staging

    output_dir = (
        Path(os.environ[RACE_OUTPUT_DIR_ENV]) if os.environ.get(RACE_OUTPUT_DIR_ENV) else tmp_path
    )
    db_path = output_dir / "rides.db"
    exports_dir = output_dir / "exports"
    output_dir.mkdir(parents=True, exist_ok=True)
    exports_dir.mkdir(parents=True, exist_ok=True)

    # E9.2.2 (R-77): the nightly owns the seed and files it on failure;
    # RIVERCROSSING_ACCEPTANCE_SEED overrides the fixed default.
    seed_env = os.environ.get("RIVERCROSSING_ACCEPTANCE_SEED")
    rng_seed = int(seed_env) if seed_env else _SIM_SEED
    ride_id = store_staging.running_ride_with_roster(
        db_path, rng_seed=rng_seed, roster=store_staging.rich_race_roster()
    )

    envelope = _run_sim_race_child(
        support,
        db_path=db_path,
        output_dir=output_dir,
        exports_dir=exports_dir,
    )
    assert envelope["ok"] is True, envelope["context"]
    assert envelope["data"] is not None, envelope["context"]
    assert envelope["data"]["ride_id"] == ride_id, envelope["context"]

    # The oracle replays the saved db independently and checks every
    # fact the child's record claims, without touching wx.
    facts = race_sim_oracle.load_race_facts(db_path)
    record = json.loads((output_dir / "race-record.json").read_text(encoding="utf-8"))

    # Sufficiency guard: the race must have actually recorded crossings
    # and driven every correction path, so a broken typed-entry path
    # cannot pass vacuously (adversarial-review fix).
    actions = record["audit_actions"]
    assert len(record["dealt_cards"]) >= 20, "no meaningful deals recorded"
    assert actions.count("record_crossing") >= 15, "no typed crossings recorded"
    for action in (
        "confirm_held",
        "void_held",
        "deal_manual",
        "dnf",
        "add_crossing_at",
        "void_crossing",
        "void_card",
    ):
        assert actions.count(action) == 1, f"expected exactly one {action}"
    assert len(record["checkpoints"]) >= 12, "too few display checkpoints"

    report = race_sim_oracle.compare(facts, record, exports_dir)

    (output_dir / "analysis.json").write_text(
        json.dumps(race_sim_oracle.render_json(report), indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "analysis-report.md").write_text(
        race_sim_oracle.render_markdown(report) + "\n", encoding="utf-8"
    )

    assert report.all_pass, race_sim_oracle.render_markdown(report)
    assert len(report.checks) >= 40, "report has too few checks (regression)"

    # The four Results exports landed as real, non-empty files.
    export_paths = {target: Path(value) for target, value in record["export_paths"].items()}
    assert set(export_paths) == {
        "export_html",
        "export_pdf",
        "export_poster",
        "export_results_csv",
    }
    for path in export_paths.values():
        assert path.exists(), path
        assert path.stat().st_size > 0, path
    assert len(list(exports_dir.iterdir())) >= 4
