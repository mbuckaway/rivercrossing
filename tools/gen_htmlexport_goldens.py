# SPDX-License-Identifier: GPL-3.0-only
"""Freeze the results-page HTML goldens and payload fixtures (E6.2.2).

The two golden samples in ``design/exports/`` are hand-assembled
(Spec §8); their ``race-data`` JSON blocks are the fixture source. This
generator rebuilds both frozen pages at
``tests/unit/fixtures/htmlexport/`` from the real renderer through the
``_render_payload`` seam -- the one deliberate regeneration TB-5
permits -- together with the ``payload-*.json`` fixtures the
byte-for-byte tests parse. Value-parity with the samples' parsed JSON
is checked before anything is written: ``record -> RacePayload ->
record`` must be identity, so a renderer change that would alter the
embedded record fails generation rather than silently freezing
different data.

    python tools/gen_htmlexport_goldens.py               # regenerate
    python tools/gen_htmlexport_goldens.py --out-dir DIR # elsewhere
    python tools/gen_htmlexport_goldens.py --check       # fail on drift

``--samples-dir``/``--out-dir`` overrides let tests point the
generator at fixture trees instead of the real ones.
"""

import argparse
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from rivercrossing.htmlexport import (
    _TRANSPARENT_PNG,
    RacePayload,
    _payload_from_record,
    _render_payload,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLES_DIR = _ROOT / "design" / "exports"
DEFAULT_OUT_DIR = _ROOT / "tests" / "unit" / "fixtures" / "htmlexport"

_RACE_DATA_RE = re.compile(
    r'<script type="application/json" id="race-data">(.*?)</script>', re.DOTALL
)

# (sample page, payload fixture, golden page) -- the three-layer freeze.
GOLDEN_SPECS: tuple[tuple[str, str, str], ...] = (
    ("epic-2026-results.html", "payload-times.json", "epic-2026-results.html"),
    (
        "epic-2026-results-no-times.html",
        "payload-no-times.json",
        "epic-2026-results-no-times.html",
    ),
)


def _race_data_block(html: str) -> str:
    """Return the text inside a results page's ``race-data`` block.

    Raises:
        ValueError: *html* has no ``race-data`` block.
    """
    match = _RACE_DATA_RE.search(html)
    if match is None:
        msg = "no race-data block found in page"
        raise ValueError(msg)
    return match.group(1)


def payload_and_record(
    samples_dir: Path, sample_name: str
) -> tuple[RacePayload, dict[str, object]]:
    """Parse one sample's ``race-data`` and build its payload.

    Raises:
        ValueError: The sample's record does not round-trip through the
            payload model, or the page has no ``race-data`` block.
    """
    html = (samples_dir / sample_name).read_text(encoding="utf-8")
    record = json.loads(_race_data_block(html))
    payload = _payload_from_record(record)
    if payload.to_record() != record:
        msg = f"value-parity failed for {sample_name}: record does not round-trip"
        raise ValueError(msg)
    return payload, record


def write_goldens(samples_dir: Path, out_dir: Path) -> tuple[Path, ...]:
    """Regenerate the payload fixtures and golden pages into *out_dir*.

    All samples are parsed and value-checked before anything is
    written, so a failure mid-tree leaves *out_dir* untouched. Returns
    the paths written; byte-identical regeneration is the contract,
    and the committed tree must reproduce exactly.
    """
    pending: list[tuple[Path, str]] = []
    for sample_name, fixture_name, golden_name in GOLDEN_SPECS:
        payload, record = payload_and_record(samples_dir, sample_name)
        pending.append(
            (
                out_dir / fixture_name,
                json.dumps(record, indent=2, ensure_ascii=False) + "\n",
            )
        )
        pending.append(
            (
                out_dir / golden_name,
                _render_payload(
                    payload,
                    dev=False,
                    logo_src=_TRANSPARENT_PNG,
                    generated=payload.event.generated,
                ),
            )
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for path, content in pending:
        path.write_text(content, encoding="utf-8")
        written.append(path)
    return tuple(written)


def drift_lines(committed: Path, regenerated: bytes, name: str) -> list[str]:
    """Return drift lines for one artifact, [] when identical."""
    if not committed.exists():
        return [f"{name} missing from {committed.parent}"]
    if committed.read_bytes() != regenerated:
        return [f"{name} drifted from {committed}"]
    return []


def check_goldens(samples_dir: Path, out_dir: Path) -> list[str]:
    """Regenerate into a scratch dir and diff against *out_dir*.

    Returns drift lines, [] when the committed files match a fresh
    generation -- the CI staleness gate for the frozen goldens.
    """
    with tempfile.TemporaryDirectory() as scratch:
        regenerated = write_goldens(samples_dir, Path(scratch))
        return [
            line
            for path in regenerated
            for line in drift_lines(out_dir / path.name, path.read_bytes(), path.name)
        ]


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--write``/``--check`` argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true", help="regenerate the goldens")
    mode.add_argument("--check", action="store_true", help="fail the build on drift")
    parser.add_argument("--samples-dir", type=Path, default=DEFAULT_SAMPLES_DIR)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: dispatch to ``--write`` or ``--check``."""
    args = _build_parser().parse_args(argv)
    try:
        if args.write:
            written = write_goldens(args.samples_dir, args.out_dir)
            print(f"wrote {len(written)} files to {args.out_dir}")
            return 0
        return _run_check(args.samples_dir, args.out_dir)
    except (ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def _run_check(samples_dir: Path, out_dir: Path) -> int:
    """Report drift between a fresh run and the committed files."""
    diffs = check_goldens(samples_dir, out_dir)
    if not diffs:
        print(f"{out_dir} matches a fresh generation from {samples_dir}")
        return 0
    for line in diffs:
        print(f"drift: {line}")
    return 1


# logic-coverage-exempt: T-3 unreachable when loaded by
# spec_from_file_location -- the repo's established test pattern never
# executes this module as __main__, so the guard's True branch is
# never taken under coverage.
if __name__ == "__main__":
    sys.exit(main())
