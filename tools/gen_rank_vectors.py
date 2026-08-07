# SPDX-License-Identifier: GPL-3.0-only
"""Generate the E2.1.1 rank sweep fixture, tests/vectors/rank_sweep.csv.

Every one of phevaluator's 7,462 distinct natural 5-card ranks gets
exactly one representative row: a 5-card hand, its phevaluator rank,
and its hand class. The rank comes from phevaluator (the same library
``rivercrossing.hands.eval5`` wraps); the hand class comes from
``rivercrossing.hands.classify_pattern``, which computes it
independently, from the cards' own rank-multiset/flush/straight
pattern, so this fixture can catch a bug in ``eval5``'s own
phevaluator-rank-to-class table rather than merely restate it -- never
by asking ``eval5`` (or anything phevaluator-backed) directly.

    python tools/gen_rank_vectors.py             # regenerate the CSV
    python tools/gen_rank_vectors.py --out PATH  # write elsewhere

``--out`` lets tests point the generator at a scratch directory to
check that regenerating it reproduces the committed file byte-for-byte.
"""

import argparse
import itertools
import sys
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

from phevaluator.evaluator import evaluate_cards

from rivercrossing.hands import classify_pattern

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = _ROOT / "tests" / "vectors" / "rank_sweep.csv"

# Rank letters low to high; phevaluator and rivercrossing.cards.Card
# both use this same alphabet, so these codes parse with either.
_RANK_LETTERS = "23456789TJQKA"
_RANK_VALUES = {letter: value for value, letter in enumerate(_RANK_LETTERS, start=2)}
_SUIT_LETTERS = "CDHS"
DECK: tuple[str, ...] = tuple(rank + suit for rank in _RANK_LETTERS for suit in _SUIT_LETTERS)

_CSV_HEADER = ("cards", "rank", "hand_class")


class RankVectorRow(NamedTuple):
    """One representative hand for one distinct phevaluator rank."""

    cards: str
    rank: int
    hand_class: str


def _enumerate_five_card_hands() -> Iterator[tuple[str, ...]]:
    """Yield every C(52,5) five-card hand, in one fixed order."""
    return itertools.combinations(DECK, 5)


def enumerate_representative_hands() -> list[RankVectorRow]:
    """Enumerate all C(52,5) hands, keeping one row per distinct rank.

    The first hand found for a given rank (in the fixed enumeration
    order above) is its representative; output is sorted by rank so
    the result -- and the file rendered from it -- is deterministic.
    """
    by_rank: dict[int, RankVectorRow] = {}
    for combo in _enumerate_five_card_hands():
        rank: int = evaluate_cards(*combo)
        if rank in by_rank:
            continue
        ranks = [_RANK_VALUES[code[0]] for code in combo]
        suits = [code[1] for code in combo]
        hand_class = classify_pattern(ranks, suits).name
        by_rank[rank] = RankVectorRow(cards=" ".join(combo), rank=rank, hand_class=hand_class)
    return [by_rank[rank] for rank in sorted(by_rank)]


def render_csv(rows: Sequence[RankVectorRow]) -> str:
    r"""Render *rows* as CSV text, header first, ``\n`` line endings."""
    lines = [",".join(_CSV_HEADER)]
    lines.extend(f"{row.cards},{row.rank},{row.hand_class}" for row in rows)
    return "\n".join(lines) + "\n"


def write_rank_vectors(out_path: Path) -> list[RankVectorRow]:
    """Generate the full rank sweep and write it to *out_path*."""
    rows = enumerate_representative_hands()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_csv(rows), encoding="utf-8", newline="\n")
    return rows


def _build_parser() -> argparse.ArgumentParser:
    """Build the ``--out`` argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI: regenerate the rank sweep CSV at ``--out``."""
    args = _build_parser().parse_args(argv)
    rows = write_rank_vectors(args.out)
    print(f"wrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
