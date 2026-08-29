# SPDX-License-Identifier: GPL-3.0-only
"""Unit tests for tools/gen_pdfexport_fixtures.py (P7/P8, E6.3.1/2).

The generator freezes both PDF goldens at
``tests/unit/fixtures/pdfexport/`` -- the results report
``epic-2026-results.pdf`` and the one-page podium poster
``epic-2026-podium.pdf`` -- regenerated once from the real renderers
with the shared fixture dataset and the pinned aware-UTC creation
stamp (R-62/D14). Regenerating them is deliberate (spec §8b's
golden-file test); ``tools/`` is a dev-script tree, not an installed
package, so the module under test is loaded from its file path (the
pattern test_gen_htmlexport_goldens.py established).
"""

import importlib.util
from pathlib import Path
from types import ModuleType  # noqa: TC003 -- used at runtime as a return type here
from typing import TYPE_CHECKING

from pdfexport_fixtures import GOLDEN_PDF, GOLDEN_POSTER

if TYPE_CHECKING:
    import pytest

_GEN_GOLDENS_PATH = Path(__file__).resolve().parents[2] / "tools" / "gen_pdfexport_fixtures.py"


def _load_gen_goldens(path: Path) -> ModuleType:
    """Load tools/gen_pdfexport_fixtures.py by path (not a package)."""
    spec = importlib.util.spec_from_file_location("gen_pdfexport_fixtures", path)
    if spec is None or spec.loader is None:
        msg = f"could not build a module spec for {path}"
        raise ImportError(msg)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen_goldens = _load_gen_goldens(_GEN_GOLDENS_PATH)


def test_write_golden_regeneration_matches_committed_file_byte_for_byte(
    tmp_path: Path,
) -> None:
    """Regenerating the report reproduces the committed golden exactly.

    Determinism (R-62, D14): the pinned aware-UTC creation stamp plus
    embedded fonts make a fresh run byte-identical to the file already
    committed at ``tests/unit/fixtures/pdfexport/epic-2026-results.pdf``
    -- the same honesty check test_gen_rank_vectors.py runs on the
    rank sweep.
    """
    path = gen_goldens.write_golden(tmp_path)

    assert path.read_bytes() == GOLDEN_PDF.read_bytes()


def test_write_golden_poster_regeneration_matches_committed_file_byte_for_byte(
    tmp_path: Path,
) -> None:
    """Regenerating the podium poster reproduces its committed golden.

    P8 (E6.3.2): the one-page poster is byte-deterministic under the
    same pinned aware-UTC stamp, so a fresh ``write_golden_poster``
    matches ``epic-2026-podium.pdf`` exactly.
    """
    path = gen_goldens.write_golden_poster(tmp_path)

    assert path.read_bytes() == GOLDEN_POSTER.read_bytes()


def test_main_write_with_out_dir_override_writes_the_golden(tmp_path: Path) -> None:
    """``--write --out-dir`` points the generator at a scratch tree."""
    out_dir = tmp_path / "out"

    exit_code = gen_goldens.main(["--write", "--out-dir", str(out_dir)])

    assert exit_code == 0
    assert (out_dir / "epic-2026-results.pdf").is_file()
    assert (out_dir / "epic-2026-podium.pdf").is_file()


def test_main_check_returns_zero_when_committed_golden_matches() -> None:
    """``--check`` passes when a fresh regeneration matches."""
    exit_code = gen_goldens.main(["--check"])

    assert exit_code == 0


def test_main_check_returns_one_with_drift_when_golden_modified(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A stale golden fails the build with a ``drift:`` line."""
    out_dir = tmp_path / "out"
    gen_goldens.main(["--write", "--out-dir", str(out_dir)])
    golden = out_dir / "epic-2026-results.pdf"
    golden.write_bytes(golden.read_bytes() + b"\x00")

    exit_code = gen_goldens.main(["--check", "--out-dir", str(out_dir)])

    assert exit_code == 1
    assert "drift: epic-2026-results.pdf" in capsys.readouterr().out
