# Python Review Findings — topic/fix-simulator-xrc-gate

_Generated: 2026-09-14 | Project: rivercrossing (branch: topic/fix-simulator-xrc-gate) | Type: basic | Scope: uncommitted working-tree diff_

Scope: the uncommitted working-tree diff (simulation "no window authored" self-heal, "CREATE RIDE"
no-ride status, shoe randomness/lap/docstring work, and the Card-shoe docs).

## Summary

| Severity | Count |
|---|---|
| Critical | 0 |
| High | 0 |
| Medium | 6 |
| Low | 12 |

## Findings

### Standards (Phase 1+2)
- MEDIUM — `cards.py:192` / `store/__init__.py:48` — garbled "fixed from version to version, never
  across them" phrasing. Fix: "fixed within a CPython version, not guaranteed across versions".
- MEDIUM — `test_ride_setup_contract.py:189` — `_STRUCTURE_CONTROLS` vs `_STRUCTURE_ONLY_CONTROLS`
  near-synonymous names for different sets. Fix: rename the 2-entry tuple.
- LOW — `_support.py:17` — one ~75-word module-docstring sentence. Fix: split into shorter sentences.
- LOW — `_support.py:254` — docstring names `delete_ride_dlg` (XRC name) instead of the call site
  `RideLibrary._on_delete_clicked` (same wording in `test_xrc_load_selfheal.py:118`).
- LOW — `test_xrc_load_selfheal.py:46` — `_ResourceDouble` annotations omit `| None`.
- LOW — `test_cards.py:43` — multiset oracle built from private `_fresh_deck`. Fix: build from
  `Rank`/`Suit`.
- LOW — `test_app_logging.py:406` — triplicated `_pin_no_authored_window` arrange helper. Fix:
  extract one shared helper.

### Security (Phase 3)
- MEDIUM — `_support.py:233` — `fresh_resource()` ignores `Load()` results; a parse failure is
  silent. Fix: check `Load()` result and record the failing path.
- MEDIUM — `app.py:4038` — `load_menubar` can return `None`; caller dereferences it
  (`SetMenuBar(None)`). Fix: guard with an explicit `LookupError`.
- LOW — `_support.py:261` — the fresh-resource retry has no exception guard; an `OSError`/parse
  error propagates into a wx handler. Fix: wrap and return `None`.
- LOW — `_support.py:261` — the rebuilt resource is not memoized; every miss re-parses all `.xrc`.
  Fix: cache the rebuilt resource at module scope.
- LOW — `test_xrc_load_selfheal.py` — no test exercises the real rebuild. Fix: add a headless test
  of `fresh_resource()`.

### Simplification (Phase 4c)
- LOW — `_support.py:264` — `load_menubar` extracted at one call site. Disposition: **RETAIN** — its
  `None`-on-both-miss contract is unit-tested (`test_xrc_load_selfheal.py`) and is exactly what the
  new `build_main_window` menubar `None` guard depends on; inlining would lose that tested seam and
  fold the retry into the bootstrap. Recorded, not deferred.

### Docs (Phase 4d)
- MEDIUM — `SHOWMECHANICS.md:68` / `user-guide.html:221` — "live shoe can never silently diverge"
  is false today: a DRAFT shoe-size edit writes the stored config + engine config but not the live
  shoe. Fix: make it true in code (`RideEngine.update_config` rebuilds the shoe on a DRAFT
  shoe-structure change) and keep the wording.
- LOW — `SHOWMECHANICS.md:17` — line citations drifted. Fix: update to final line numbers.
- LOW — `SHOWMECHANICS.md:89` — References paths missing `src/` prefix.
- LOW — `user-guide.html:213` — new section is unnumbered, breaking the numbered-TOC alignment.
  Fix: number it "10 · Card shoe …" and renumber "10 · Troubleshooting & FAQ" → "11 ·".

## Resolutions (2026-09-14)

Every finding above was fixed on this branch; the load_menubar retention is the one disposition
that keeps code rather than changing it.

| # | Finding | Resolution |
|---|---|---|
| 1 | Garbled CPython shuffle wording | Reworded to "the algorithm is fixed within a CPython version, but not guaranteed across versions" in `cards.py` and `store/__init__.py`. |
| 2 | Near-synonymous control tuples | `_STRUCTURE_ONLY_CONTROLS` → `_GATE_ONLY_CONTROLS` (both references updated). |
| 3 | 75-word docstring sentence | Split into three sentences in `_support.py`'s module docstring. |
| 4 | Wrong call site named | `delete_ride_dlg` → `RideLibrary._on_delete_clicked` in `_support.py` and `test_xrc_load_selfheal.py`. |
| 5 | `_ResourceDouble` annotations | `window: object \| None = None`; both load methods return `object \| None`. |
| 6 | Oracle from private `_fresh_deck` | Rebuilt from the public `Rank`/`Suit` enums plus the configured joker count; private import dropped. |
| 7 | Triplicated arrange helper | New shared `tests/unit/ui/xrc_fixtures.py::pin_no_authored_window(monkeypatch, factory)`; all three modules call it with their own absent-resource factory. |
| 8 | `fresh_resource()` ignored `Load()` | Each `Load()` result is checked; a failure records the path through `wx.LogWarning`. The rebuilt resource is memoized per directory (`functools.cache`), and `fresh_resource(xrc_dir=…)` defaults to the packaged `ui/xrc`. |
| 9 | `SetMenuBar(None)` dereference | `build_main_window` raises `LookupError(f"no menubar named {ids.MAIN_MENUBAR!r}")` before `SetMenuBar` and the two `_check_loaded_*` calls. |
| 10 | Unguarded rebuild retry | Both `load_dialog` and `load_menubar` wrap the `fresh_resource()` retry in `try/except Exception: return None`. |
| 11 | No real-rebuild test | Headless tests drive the real `fresh_resource(xrc_dir=…)` over temporary directories (valid, empty, unreadable; plus memoization), with a `wx.App(False)` fixture destroyed after. |
| 12 | `load_menubar` extraction | **RETAINED** (see Simplification above): the unit-tested `None`-on-both-miss contract is what the new menubar guard depends on. |
| 13 | DRAFT shoe-size edit diverged | `Shoe.reconfigure(decks, jokers_per_deck, *, jokers_total)` rebuilds cycle 1 from the stored seed; `RideEngine.update_config` calls it when DRAFT and the shoe structure changed. Tests first in `test_cards.py`/`test_ride.py`. |
| 14 | Drifted line citations | `SHOWMECHANICS.md` now cites `store/__init__.py:980`, `cards.py:321-322`, `cards.py:341`, and the five `ride.py` ranges shifted by the reconfigure work (2326-2331, 1596-1597, 1762-1767, 2099-2114, 1965-1983). |
| 15 | References paths | All three References paths now carry the `src/` prefix. |
| 16 | Unnumbered card-shoe section | Heading is "10 · Card shoe — how cards are dealt"; Troubleshooting renumbered to "11 ·". TOC chapters 1–11 then match the headings; the two appendix entries keep their unnumbered headings. |
