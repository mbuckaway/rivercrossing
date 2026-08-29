# SPDX-License-Identifier: GPL-3.0-only
"""Freeze the PDF results golden (P7, E6.3.1).

``pdfexport.render`` is deterministic (R-62, D14): identical inputs
plus the pinned aware-UTC creation stamp produce byte-identical
files, so the report at
``tests/unit/fixtures/pdfexport/epic-2026-results.pdf`` can be frozen
and regenerated honestly -- the deliberate regeneration TB-5 permits
for the HTML goldens, in the same spirit as gen_rank_vectors.py's
sweep. The dataset builder lives in ``tests/unit/pdfexport_fixtures.py``
(the golden dataset, ride and options), shared with the byte-for-byte
test so the generator and the test cannot drift apart on inputs.

    python tools/gen_pdfexport_fixtures.py              # regenerate
    python tools/gen_pdfexport_fixtures.py --out-dir DIR # elsewhere
    python tools/gen_pdfexport_fixtures.py --check      # fail on drift

``--out-dir`` lets tests point the generator at a scratch directory
to check that regenerating reproduces the committed file byte-for-byte.
"""

import argparse
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from rivercrossing.pdfexport import render

if TYPE_CHECKING:
    from collections.abc import Sequence

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT_DIR = _ROOT / "tests" / "unit" / "fixtures" / "pdfexport"

# The generator's input builder lives under tests/unit/ (the repo's
# established shared-fixture location); importing it by path keeps
# tools/ independent of the installed package layout, the same pattern
# tools/gen_ids.py and its test use for tests/ modules.
sys.path.insert(0, str(_ROOT / "tests" / "unit"))

from pdfexport_fixtures import (  # noqa: E402 -- needs the path above
    FIXED_CREATED,
    GOLDEN_PDF,
    build_placed,
    build_ride,
    golden_opts,
)


def write_golden(out_dir: Path) -> Path:
    """Regenerate the golden report into *out_dir*; return its path.

    Raises:
        ValueError: A render could not be produced (a changed dataset
            that no longer satisfies the renderer's invariants).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / GOLDEN_PDF.name
    render(build_ride(), build_placed(), golden_opts(), path, created_at=FIXED_CREATED)
    return path


def drift_lines(committed: Path, regenerated: bytes, name: str) -> list[str]:
    """Return drift lines for one artifact, [] when identical."""
    if not committed.exists():
        return [f"{name} missing from {committed.parent}"]
    if committed.read_bytes() != regenerated:
        return [f"{name} drifted from {committed}"]
    return []


def check_golden(out_dir: Path) -> list[str]:
    """Regenerate into a scratch dir and diff against *out_dir*.

    Returns drift lines, [] when the committed file matches a fresh
    generation -- the CI staleness gate for the frozen golden.
    """
    with tempfile.TemporaryDirectory() as scratch:
        regenerated = write_golden(Path(scratch))
        return drift_lines(out_dir / regenerated.name, regenerated.read_bytes(), regenerated.name)


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--write``/``--check`` argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the golden")
    mode.add_argument("--check", action="store_true", help="fail the build on drift")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def _run_check(out_dir: Path) -> int:
    """Report drift between a fresh run and the committed file."""
    diffs = check_golden(out_dir)
    if not diffs:
        print(f"{out_dir} matches a fresh generation")
        return 0
    for line in diffs:
        print(f"drift: {line}")
    return 1


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: dispatch to ``--write`` or ``--check``."""
    args = _build_parser().parse_args(argv)
    try:
        if args.write:
            path = write_golden(args.out_dir)
            print(f"wrote {path}")
            return 0
        return _run_check(args.out_dir)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


# logic-coverage-exempt: T-3 unreachable when loaded by
# spec_from_file_location -- the repo's established test pattern never
# executes this module as __main__, so the guard's True branch is
# never taken under coverage.
if __name__ == "__main__":
    sys.exit(main())
