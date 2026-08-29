# SPDX-License-Identifier: GPL-3.0-only
"""Shared fixture paths and loaders for the results-page tests (E6.2.2).

Both ``test_payload.py`` and ``test_htmlexport.py`` parse the same
three layers of results-page data:

* ``design/exports/*.html`` -- the two hand-assembled golden SAMPLES;
  their ``race-data`` JSON blocks are the fixture source (AGENTS.md).
* ``tests/unit/fixtures/htmlexport/payload-*.json`` -- the same JSON
  content extracted to stable fixture files, so the byte-for-byte
  goldens and the JSON round-trip tests never depend on ``design/``.
* ``tests/unit/fixtures/htmlexport/epic-2026-results*.html`` -- the
  frozen GOLDENS regenerated once by ``tools/gen_htmlexport_goldens.py``
  from the real renderer (TB-5); ``test_htmlexport.py`` compares
  ``_render_payload`` output to them byte-for-byte.

``race_payload_from_record`` lives in ``rivercrossing.htmlexport``
(the inverse of ``RacePayload.to_record``) and is reused by the golden
generator; this module only locates files and parses the ``race-data``
block.
"""

import json
import re
from pathlib import Path

from rivercrossing.htmlexport import RacePayload, _payload_from_record

_ROOT = Path(__file__).resolve().parents[2]

SAMPLES_DIR = _ROOT / "design" / "exports"
FIXTURES_DIR = _ROOT / "tests" / "unit" / "fixtures" / "htmlexport"

TIMES_SAMPLE = SAMPLES_DIR / "epic-2026-results.html"
NO_TIMES_SAMPLE = SAMPLES_DIR / "epic-2026-results-no-times.html"

TIMES_FIXTURE = FIXTURES_DIR / "payload-times.json"
NO_TIMES_FIXTURE = FIXTURES_DIR / "payload-no-times.json"

GOLDEN_TIMES = FIXTURES_DIR / "epic-2026-results.html"
GOLDEN_NO_TIMES = FIXTURES_DIR / "epic-2026-results-no-times.html"

_RACE_DATA_RE = re.compile(
    r'<script type="application/json" id="race-data">(.*?)</script>', re.DOTALL
)


def race_data_block(html: str) -> str:
    """Return the text inside a rendered page's ``race-data`` block.

    Raises:
        AssertionError: *html* has no ``race-data`` block -- a page
            that does not embed the record is not a results page.
    """
    match = _RACE_DATA_RE.search(html)
    if match is None:
        msg = "no race-data block found in page"
        raise AssertionError(msg)
    return match.group(1)


def parse_race_data(path: Path) -> dict[str, object]:
    """Extract and parse a results page's ``race-data`` JSON block."""
    return json.loads(race_data_block(path.read_text(encoding="utf-8")))


def load_race_payload(path: Path) -> RacePayload:
    """Read one committed ``payload-*.json`` fixture as a payload."""
    return _payload_from_record(json.loads(path.read_text(encoding="utf-8")))
