# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/gen_rank_vectors.py (E2.1.1).

``tools/`` is a dev-script tree, not an installed package (mirrors
tools/gen_ids.py and its test_ids_gen.py), so the module under test is
loaded from its file path rather than imported by dotted name.
"""

import importlib.util
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here

_GEN_RANK_VECTORS_PATH = Path(__file__).resolve().parents[2] / "tools" / "gen_rank_vectors.py"


def _load_gen_rank_vectors(path: Path) -> ModuleType:
    """Load tools/gen_rank_vectors.py by path -- it isn't a package."""
    spec = importlib.util.spec_from_file_location("gen_rank_vectors", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_rank_vectors = _load_gen_rank_vectors(_GEN_RANK_VECTORS_PATH)

_COMMITTED_CSV = Path(__file__).resolve().parents[2] / "tests" / "vectors" / "rank_sweep.csv"


def test_write_rank_vectors_regeneration_matches_committed_csv_byte_for_byte(
    tmp_path: Path,
) -> None:
    """Regenerating the sweep into a scratch dir reproduces it exactly.

    Enumerating all C(52,5) hands is deterministic given a fixed deck
    order (module docstring), so a fresh run must match the file
    already committed at ``tests/vectors/rank_sweep.csv`` byte for
    byte -- the same honesty check test_ids_gen.py runs on ids.py.
    """
    out_path = tmp_path / "rank_sweep.csv"

    gen_rank_vectors.write_rank_vectors(out_path)

    assert out_path.read_bytes() == _COMMITTED_CSV.read_bytes()
