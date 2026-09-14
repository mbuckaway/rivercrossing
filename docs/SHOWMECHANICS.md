# RiverCrossing — Card Shoe Mechanics

How the seeded multi-deck shoe is created, shuffled, dealt, and repaired, and why the shuffle is
random. This is the engineering companion to the user guide's "Card shoe" section; the code
references name the current implementation.

## Overview

- One ride has one shoe: a single shuffled list of cards that every crossing deals from.
- The shoe is **seeded**. The seed is a cryptographically random 63-bit integer generated once when
  the ride is created and stored in the `ride.rng_seed` database column. Rebuilding the shoe from the
  same `(deck_count, jokers_per_deck, jokers_mode, rng_seed)` reproduces the identical deal order —
  the R-40 replay guarantee that survives a crash or restart without ever storing the shuffled list.

## Creation

- `Store.create_ride` draws the seed with `secrets.randbits(63)` (`store/__init__.py:980`) — the OS
  CSPRNG (`os.urandom`), the "truly random" source in Python. The seed is never taken from user
  config.
- A `Shoe` is built from the stored config + seed whenever the `RideEngine` is constructed
  (`Store.load_engine`, and the test-only `_build_console_engine`). It is not re-seeded for the life
  of the ride.

## Composition

- Per spec §4 the shoe holds `deck_count × (52 + jokers_per_deck)` cards in `per_deck` mode, or
  `deck_count × 52 + jokers_per_deck` in `total` mode (jokers spent once across the ride).
- `cards._shuffled_sequence` builds the card list and shuffles it with
  `random.Random(seed).shuffle` — CPython's Durstenfeld (Fisher–Yates) shuffle, an unbiased
  permutation (no modulo bias since Python 3.2's rejection-sampling `randrange`).

## Dealing

- `Shoe.deal()` always returns `self._cards[self._dealt]` — the **front** of the remaining list —
  then increments `dealt` (`cards.py:321-322`). The next card is the next index; there is no "top of
  deck vs bottom" ambiguity: one flat list, dealt in order 0, 1, 2, ….

## Exhaustion &amp; reshuffle

- `RideEngine._deal_card()` catches `ShoeEmpty`, calls `Shoe.reshuffle()` (a new cycle shuffled under
  `seed + (cycle - 1)`), appends a `shoe_reshuffle` audit event, then deals again
  (`ride.py:2326-2331`). The caller never deals from an empty shoe.

## Returning a card (undo only)

- `Shoe.restitute(card)` decrements `dealt` — the card returns to the **front**, so the next deal
  re-deals it (`cards.py:341`). Only the most recently dealt card can be restituted; anything
  else raises `RestitutionError` (silently rewriting history is forbidden).
- `RideEngine.undo_last()` restitutes when possible. When it cannot — the shoe is closed after
  Finish, or a later manual deal put a different card at the front — the undone card **retires**
  deterministically instead, and replay still reproduces the same shoe point (`ride.py:1596-1597`).

## Voiding (crossing or card)

- `void_crossing` and `void_card` do **not** return the card to the shoe; the card is voided out of
  the system (`card.state='voided'`). Restitution is `undo_last`'s job, never the void commands'
  (`ride.py:1762-1767`, `2099-2114`).

## Reassigning a crossing

- `reassign_crossing` moves the crossing — and its card — to the destination entry. The card
  travels; it is never re-dealt (`ride.py:1965-1983`).

## Edit-gating

- Shoe structure (`deck_count`, `jokers_per_deck`, `jokers_mode`) is DRAFT-only.
  `can_edit_structure` returns true only for `RideStatus.DRAFT` (`roster.py:328-334`), and the Edit
  Ride dialog disables those controls past DRAFT (`ride_setup.py:641-662`), so the live shoe can
  never silently diverge from the stored seed's replay.

## Randomness

- The seed is drawn from the OS CSPRNG (`secrets.randbits(63)`), the "truly random" source.
- The shuffle is Fisher–Yates, an unbiased permutation.
- **Reachability note:** a 63-bit seed means the shoe reaches at most 2⁶³ distinct orders — a tiny
  fraction of the `n!` possible permutations of a 52-card deck. That is statistically
  indistinguishable from uniform for a poker run, and the seed is unguessable, so it is not a
  defect. The 2080-card Mersenne-Twister period note in the `random` docs is irrelevant here: the
  **seed**, not the generator period, is the actual reachability ceiling.
- Determinism from the stored seed is the R-40 replay guarantee, not a randomness flaw.
  `secrets.SystemRandom().shuffle` would be "more random" (no seed ceiling) but is non-reproducible
  and would break replay.
- Replay determinism is pinned to the running Python version: the compatible seeder guarantees the
  same `random()` stream for the same integer seed, but the `shuffle()` algorithm itself is not
  version-guaranteed across CPython releases.

## References

- `src/rivercrossing/cards.py` — `Shoe`, `Card`, `_shuffled_sequence`.
- `src/rivercrossing/ride.py` — `RideEngine._deal_card`, `undo_last`, `void_crossing`, `void_card`,
  `reassign_crossing`, `finish`/`reopen`.
- `src/rivercrossing/store/__init__.py` — `create_ride` (seed), `load_engine` (shoe rebuild).
- Python docs: `secrets`, `random` (Fisher–Yates `shuffle`, `SystemRandom`, reproducibility notes).
