# SPDX-License-Identifier: GPL-3.0-only
"""Format the nightly acceptance failure issue body (E9.2.2, R-77).

``.github/workflows/nightly.yml`` generates a random seed, runs the
R-74 acceptance race with it (``RIVERCROSSING_ACCEPTANCE_SEED``), and
on failure pipes this script's stdout into ``gh issue create
--body-file -``. The body carries the seed verbatim so the failed night
is reproducible by re-running the race with the same env value.
"""

import argparse
import sys

__all__ = ["build_issue_body", "main"]


def build_issue_body(*, seed: int, os_label: str, run_url: str) -> str:
    """Return the issue body text for a failed nightly acceptance run.

    Args:
        seed: The seed the failed race ran with.
        os_label: The runner leg that failed (for example, macOS).
        run_url: The GitHub Actions run URL.

    Returns:
        The body, with the seed on its own line so a reader (or a
        re-run) can copy it straight out.
    """
    return (
        f"The nightly acceptance race failed on {os_label}.\n\n"
        f"Seed: {seed}\n\n"
        "Reproduce by re-running the race with "
        f"RIVERCROSSING_ACCEPTANCE_SEED={seed}.\n\n"
        f"Run: {run_url}"
    )


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--seed``/``--os``/``--run-url`` argument parser."""
    parser = argparse.ArgumentParser(description="Format the nightly failure issue body.")
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--os", dest="os_label", required=True)
    parser.add_argument("--run-url", dest="run_url", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Print the issue body to stdout; return 0 on success.

    Printing stdout *is* the whole contract: the workflow pipes it into
    ``gh issue create --body-file -``, and the unit test (the brief's
    dry-run) asserts the seed lands in the body.
    """
    args = _build_parser().parse_args(argv)
    print(build_issue_body(seed=args.seed, os_label=args.os_label, run_url=args.run_url))
    return 0


if __name__ == "__main__":
    sys.exit(main())
