# SPDX-License-Identifier: GPL-3.0-only
"""Freeze the PDF results goldens (P7, E6.3.1 / P8, E6.3.2).

``pdfexport.render`` and ``pdfexport.podium_poster`` are deterministic
(R-62, D14): identical inputs plus the pinned aware-UTC creation stamp
produce byte-identical files, so the report at
``tests/unit/fixtures/pdfexport/epic-2026-results.pdf`` and the
one-page poster at ``epic-2026-podium.pdf`` can be frozen and
regenerated honestly -- the deliberate regeneration TB-5 permits for
the HTML goldens, in the same spirit as gen_rank_vectors.py's sweep.
The dataset builder lives in ``tests/unit/pdfexport_fixtures.py``
(the golden dataset, ride and options), shared with the byte-for-byte
tests so the generator and the tests cannot drift apart on inputs.

    python tools/gen_pdfexport_fixtures.py              # regenerate
    python tools/gen_pdfexport_fixtures.py --out-dir DIR
    python tools/gen_pdfexport_fixtures.py --check      # fail on drift

``--out-dir`` lets tests point the generator at a scratch directory
to check that regenerating reproduces the committed files byte-for-byte.
"""

import argparse
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from rivercrossing.pdfexport import podium_poster, render

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
    GOLDEN_POSTER,
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


def write_golden_poster(out_dir: Path) -> Path:
    """Regenerate the golden podium poster into *out_dir*; return it.

    Raises:
        ValueError: A render could not be produced (a changed dataset
            that no longer satisfies the renderer's invariants).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / GOLDEN_POSTER.name
    podium_poster(build_ride(), build_placed(), path, created_at=FIXED_CREATED)
    return path


def drift_lines(committed: Path, regenerated: bytes, name: str) -> list[str]:
    """Return drift lines for one artifact, [] when identical."""
    if not committed.exists():
        return [f"{name} missing from {committed.parent}"]
    if committed.read_bytes() != regenerated:
        return [f"{name} drifted from {committed}"]
    return []


def check_golden(out_dir: Path) -> list[str]:
    """Regenerate both goldens into a scratch dir and diff *out_dir*.

    Returns drift lines, [] when the committed files match fresh
    generations -- the CI staleness gate for the frozen goldens.
    """
    with tempfile.TemporaryDirectory() as scratch:
        scratch_dir = Path(scratch)
        report = write_golden(scratch_dir)
        poster = write_golden_poster(scratch_dir)
        return drift_lines(out_dir / report.name, report.read_bytes(), report.name) + drift_lines(
            out_dir / poster.name, poster.read_bytes(), poster.name
        )


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--write``/``--check`` argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the goldens")
    mode.add_argument("--check", action="store_true", help="fail the build on drift")
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def _run_check(out_dir: Path) -> int:
    """Report drift between a fresh run and the committed files."""
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
            report_path = write_golden(args.out_dir)
            poster_path = write_golden_poster(args.out_dir)
            print(f"wrote {report_path}")
            print(f"wrote {poster_path}")
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
