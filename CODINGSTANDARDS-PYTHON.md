# Python Coding Constitution - PEP 8 Style Guide

**Based on PEP 8 – Style Guide for Python Code**

This document establishes the coding standards for all Python code, based on the official Python Enhancement Proposal 8 (PEP 8).

## Core Philosophy

> "Code is read much more often than it is written." - Guido van Rossum

**Key Principles:**
- **Readability counts** - Code should be optimized for human comprehension
- **Consistency is critical** - Within a project, module, and function
- **Practicality beats purity** - Know when to be inconsistent

## Code Layout

### Indentation

**REQUIRED:** Use **4 spaces** per indentation level. Never mix tabs and spaces.

```python
# Correct: Aligned with opening delimiter
foo = long_function_name(var_one, var_two,
                         var_three, var_four)

# Correct: Hanging indent with extra level
def long_function_name(
        var_one, var_two, var_three,
        var_four):
    print(var_one)

# Wrong: Arguments on first line without vertical alignment
foo = long_function_name(var_one, var_two,
    var_three, var_four)
```

### Maximum Line Length

**REQUIRED:**
- **79 characters** maximum for code
- **72 characters** maximum for comments and docstrings

**ALLOWED:** Teams may increase to **99 characters** for code (not comments) if agreed upon.

### Line Breaking

**PREFERRED:** Break before binary operators (following mathematical tradition):

```python
# Correct: operators aligned with operands
income = (gross_wages
          + taxable_interest
          + (dividends - qualified_dividends)
          - ira_deduction
          - student_loan_interest)

# Wrong: operators far from operands
income = (gross_wages +
          taxable_interest +
          (dividends - qualified_dividends) -
          ira_deduction -
          student_loan_interest)
```

### Blank Lines

**REQUIRED:**
- **2 blank lines** around top-level functions and class definitions
- **1 blank line** around method definitions inside classes
- Use blank lines sparingly within functions to indicate logical sections

### Imports

**REQUIRED:** Imports must be:
- At the top of the file (after module docstring, before globals)
- On separate lines (except `from` imports)
- Grouped in order: standard library, third-party, local imports
- Separated by blank lines between groups

```python
# Correct
import os
import sys
from subprocess import Popen, PIPE

# Wrong
import sys, os
```

**Grouping Order:**
1. Standard library imports
2. Related third party imports
3. Local application/library specific imports

**PREFERRED:** Use absolute imports:
```python
import mypkg.sibling
from mypkg import sibling
from mypkg.sibling import example
```

**PROHIBITED:** Wildcard imports (`from module import *`)

### Source File Encoding

**REQUIRED:** UTF-8 encoding (Python 3 default). No encoding declarations needed.

## Python Version & Forbidden Python 2.x Syntax

**REQUIRED:** All Python source MUST target **Python 3.14+ exclusively**. Python 2 went end-of-life on 2020-01-01; we never run it, never test on it, and never accept code that only made sense in Py2.

### Forbidden Python 2.x constructs

If any of these appear in a source file, the file is rejected:

| Forbidden (Python 2.x) | Required (Python 3) |
| --- | --- |
| `print "hello"` (statement) | `print("hello")` (function call) |
| `except X, e:` (single exception with comma bind) | `except X as e:` (parens with `as` are mandatory per PEP 758) |
| `raise X, "msg"` | `raise X("msg")` |
| `raise X, "msg", tb` | `raise X("msg").with_traceback(tb)` |
| `xrange(n)` | `range(n)` |
| `dict.iteritems()` / `.iterkeys()` / `.itervalues()` | `.items()` / `.keys()` / `.values()` |
| `dict.has_key(k)` | `k in dict` |
| `<>` operator | `!=` |
| `basestring`, `unicode`, `long` builtins | `str`, `int` |
| `import urllib2` / `import urlparse` / `import StringIO` | `import urllib.request` / `import urllib.parse` / `from io import StringIO` |
| `from __future__ import print_function / absolute_import / unicode_literals / division` | remove — these are no-ops on Python 3 |
| `# -*- coding: utf-8 -*-` header | remove — UTF-8 is the Python 3 default |
| `u"text"` unicode literal prefix | `"text"` (str is unicode in Py3) |

**ALLOWED on Python 3:** `from __future__ import annotations` (PEP 563 / lazy annotation evaluation). This is the only `__future__` import that still has meaning.

### Explicitly accepted Python 3.14 / PEP 758 syntax (DO NOT confuse with Python 2.x)

[PEP 758](https://peps.python.org/pep-0758/) (Final, accepted 2025-03-14, shipped in Python 3.14) permits **unparenthesized multi-exception `except` clauses** when no `as` clause is present. This LOOKS like Python 2.x syntax but is valid, modern Python 3.14 code:

```python
# VALID Python 3.14 (PEP 758) — DO NOT flag as a SyntaxError or "Python 2.x leftover"
try:
    cf.create_invalidation(...)
except ClientError, BotoCoreError, OSError:
    log_warning("CloudFront invalidation failed")

# Parentheses ARE still required when binding with `as` (PEP 758 keeps this):
try:
    ...
except (ClientError, BotoCoreError) as exc:
    ...
```

Ruff v0.15.0+ with `target-version = "py314"` **actively strips** parentheses from `except (A, B):` to enforce the new form (see [Ruff v0.15.0 release notes](https://astral.sh/blog/ruff-v0.15.0)). Adding parentheses back is contrary to the project's enforced style.

**Reviewer note** — GitHub Copilot's MR reviewer has produced verdicts like:

> "Invalid exception syntax: `except subprocess.CalledProcessError, FileNotFoundError:` raises a SyntaxError in Python 3+. Use `except (subprocess.CalledProcessError, FileNotFoundError):` (and optionally bind `as exc` if needed)."

This verdict is **incorrect** for any project targeting Python 3.14+. The code in question is valid PEP 758 syntax; `ast.parse` accepts it and CPython executes it. Reject the suggestion and cite PEP 758. See your repository's Copilot custom-instructions file for the project-side directive that tells Copilot to stop generating this false positive. (On GitHub this lived at `.github/copilot-instructions.md`; confirm the equivalent path for the GitLab + Copilot setup — see `FOLLOWUP.md`.)

### Python 3.x syntax enforcement

- **Ruff `pyupgrade` (UP) rules** are the primary automated gate. With `select = ["ALL"]` and `target-version = "py314"`, ruff catches:
  - UP008 `super-call-with-parameters` — Py2-style `super(Cls, self)` → `super()`
  - UP024 `os-error-alias` — `IOError` / `WindowsError` → `OSError`
  - UP025 `unicode-kind-prefix` — `u"..."` → `"..."`
  - UP028 `yield-in-for-loop` — `for x in y: yield x` → `yield from y`
  - UP010 unused `__future__` imports (other than `annotations`)
  - UP030 / UP032 — old `%` / `.format()` calls → f-strings
  - UP040 — pre-PEP 695 `TypeAlias` declarations
- **Python parser** rejects most genuine Py2 syntax (print statement, `raise X, msg`, `<>`, `xrange` as a name, etc.) before lint even runs.
- **mypy** does NOT have Python-2-to-3 modernization rules; it relies on CPython's parser for syntax.
- **Manual review** is required for directories excluded from ruff (e.g. `scripts/`, or vendored/generated code directories). Authors of code in those trees MUST hand-audit against this section.

## String Quotes

**REQUIRED:** Be consistent within a project. Use the opposite quote type to avoid backslashes.

**REQUIRED:** Triple-quoted strings must use double quotes (`"""`) to align with docstring conventions.

## Whitespace in Expressions

### Pet Peeves - AVOID

**NO whitespace:**
- Inside parentheses, brackets, or braces: `spam(ham[1], {eggs: 2})`
- Before commas, semicolons, or colons: `if x == 4: print(x, y); x, y = y, x`
- Before function call parentheses: `spam(1)` not `spam (1)`
- Before indexing brackets: `dct['key']` not `dct ['key']`

**Correct slice spacing:**
```python
# Correct
ham[1:9], ham[1:9:3], ham[:9:3], ham[1::3]
ham[lower:upper], ham[lower+offset : upper+offset]

# Wrong
ham[1: 9], ham[1 :9], ham[1:9 :3]
```

### Required Whitespace

**REQUIRED:** Single space on both sides of:
- Assignment operators: `=`, `+=`, `-=`, etc.
- Comparisons: `==`, `<`, `>`, `!=`, `<=`, `>=`, `in`, `not in`, `is`, `is not`
- Booleans: `and`, `or`, `not`

```python
# Correct
i = i + 1
submitted += 1
x = x*2 - 1
hypot2 = x*x + y*y
c = (a+b) * (a-b)

# Wrong
i=i+1
submitted +=1
x = x * 2 - 1
```

**Function annotations:**
```python
# Correct
def munge(input: AnyStr) -> PosInt: ...

# Wrong
def munge(input:AnyStr)->PosInt: ...
```

**Keyword arguments:**
```python
# Correct
def complex(real, imag=0.0):
    return magic(r=real, i=imag)

# With annotations and defaults
def munge(input: AnyStr, sep: AnyStr = None): ...

# Wrong
def complex(real, imag = 0.0):
    return magic(r = real, i = imag)
```

### Trailing Commas

**REQUIRED:** For single-element tuples:
```python
FILES = ('setup.cfg',)
```

**RECOMMENDED:** For version-controlled multi-line structures:
```python
FILES = [
    'setup.cfg',
    'tox.ini',
]
```

## Comments

### General Rules

**REQUIRED:**
- Comments must be complete sentences
- First word capitalized (unless it's an identifier)
- Comments contradicting code are worse than no comments
- Keep comments up-to-date with code changes
- Write in English for open-source projects

### Block Comments

**FORMAT:**
- Apply to code that follows
- Indented to same level as code
- Each line starts with `#` and single space
- Paragraphs separated by line with single `#`

### Inline Comments

**USE SPARINGLY** - Must be separated by at least 2 spaces from statement:

```python
x = x + 1  # Compensate for border
```

**PROHIBITED:** Obvious inline comments:
```python
x = x + 1  # Increment x  # DON'T DO THIS
```

### Documentation Strings (Docstrings)

**REQUIRED:** Write docstrings for all public modules, functions, classes, and methods.

**FORMAT:**
```python
"""Return a foobang.

Optional plotz says to frobnicate the bizbaz first.
"""

# One-liners keep closing quotes on same line
"""Return an ex-parrot."""
```

See PEP 257 for detailed docstring conventions.

## Naming Conventions

### General Styles

- `lowercase`
- `lower_case_with_underscores`
- `UPPERCASE`
- `UPPER_CASE_WITH_UNDERSCORES`
- `CapitalizedWords` (CapWords/CamelCase)
- `mixedCase` (differs by initial lowercase)

### Specific Conventions

| Type           | Convention                              | Example             |
| -------------- | --------------------------------------- | ------------------- |
| **Modules**    | `lowercase` or `lower_with_underscores` | `mymodule.py`       |
| **Packages**   | `lowercase` (no underscores preferred)  | `mypackage`         |
| **Classes**    | `CapWords`                              | `MyClass`           |
| **Exceptions** | `CapWords` + `Error` suffix             | `ValueError`        |
| **Functions**  | `lowercase_with_underscores`            | `my_function()`     |
| **Variables**  | `lowercase_with_underscores`            | `my_variable`       |
| **Constants**  | `UPPER_CASE_WITH_UNDERSCORES`           | `MAX_OVERFLOW`      |
| **Methods**    | `lowercase_with_underscores`            | `instance_method()` |

### Special Naming Patterns

**REQUIRED:**
- `self` for first argument to instance methods
- `cls` for first argument to class methods

**Underscore Conventions:**
- `_single_leading_underscore`: weak "internal use" indicator
- `single_trailing_underscore_`: avoid keyword conflicts
- `__double_leading_underscore`: name mangling for class attributes
- `__double_leading_and_trailing__`: "magic" objects (don't invent these)

### Names to Avoid

**PROHIBITED:** Never use single characters `l` (lowercase L), `O` (uppercase o), or `I` (uppercase i) as variable names - they're indistinguishable from 1 and 0 in some fonts.

## Programming Recommendations

### Comparisons

**REQUIRED:**
```python
# Correct: Singletons
if foo is not None:

# Wrong
if not foo is None:
```

**REQUIRED:** Use `isinstance()` for type checking:
```python
# Correct
if isinstance(obj, int):

# Wrong
if type(obj) is type(1):
```

### Sequences

**PREFERRED:** Use empty sequence truth value:
```python
# Correct
if not seq:
if seq:

# Wrong
if len(seq):
if not len(seq):
```

### Boolean Comparisons

**PROHIBITED:** Don't compare to `True` or `False`:
```python
# Correct
if greeting:

# Wrong
if greeting == True:
```

### Function Definitions

**REQUIRED:** Use `def` statements, not lambda assignments:
```python
# Correct
def f(x): return 2*x

# Wrong
f = lambda x: 2*x
```

### Exception Handling

**REQUIRED:**
- Derive exceptions from `Exception`, not `BaseException`
- Be specific in exception catching:
```python
# Correct
try:
    import platform_specific_module
except ImportError:
    platform_specific_module = None

# Wrong: bare except
try:
    import platform_specific_module
except:
    platform_specific_module = None
```

**REQUIRED:** Limit `try` clauses to minimum necessary code:
```python
# Correct
try:
    value = collection[key]
except KeyError:
    return key_not_found(key)
else:
    return handle_value(value)

# Wrong
try:
    return handle_value(collection[key])
except KeyError:
    return key_not_found(key)
```

### Context Managers

**REQUIRED:** Use `with` statements for resource management:
```python
# Correct
with conn.begin_transaction():
    do_stuff_in_transaction(conn)
```

### Return Statements

**REQUIRED:** Be consistent - all return statements should return an expression, or none should:
```python
# Correct
def foo(x):
    if x >= 0:
        return math.sqrt(x)
    else:
        return None

# Wrong
def foo(x):
    if x >= 0:
        return math.sqrt(x)
```

### String Methods

**PREFERRED:** Use string methods over string module:
```python
# Correct
if foo.startswith('bar'):

# Wrong
if foo[:3] == 'bar':
```

## Type Annotations

### Function Annotations

**REQUIRED:** Use PEP 484 syntax for type hints:

```python
def greeting(name: str) -> str:
    return f'Hello {name}'
```

**REQUIRED:** Proper spacing:
```python
# Correct
def munge(input: AnyStr): ...
def munge() -> PosInt: ...

# Wrong
def munge(input:AnyStr): ...
def munge()->PosInt: ...
```

### Variable Annotations

**REQUIRED:** Spacing for variable annotations:
```python
# Correct
code: int
class Point:
    coords: Tuple[int, int]
    label: str = '<unknown>'

# Wrong
code:int  # No space after colon
code : int  # Space before colon
result: int=0  # No spaces around equality
```

## Public vs Internal Interfaces

**REQUIRED:**
- Use `__all__` to explicitly declare public API
- Prefix internal interfaces with single underscore `_`
- Document public interfaces clearly
- Assume undocumented interfaces are internal

## Enforcement

### Automated Tools

**RECOMMENDED:**

- `ruff` - Code formatting and linting (replaces black, flake8, isort)
- `mypy` - Type checking

### Code Review

All code must pass:

1. Automated linting (`ruff check .`)
2. Type checking (mypy) where applicable
3. Peer review for style adherence

## Exceptions

**ALLOWED:** Break these rules when:
1. Following the guideline would make code less readable
2. Being consistent with surrounding code that breaks the rule
3. Code predates the guideline
4. Maintaining compatibility with older Python versions

**ALWAYS PRIORITIZE:**
1. Function consistency > Module consistency > Project consistency > PEP 8
2. Readability > Rigid adherence to rules

## Testing Requirements

Tests must verify behavior, not execution. Branch coverage is a hard CI gate; the Logic Coverage Score (LCS) is the advisory rigor signal that tests can actually distinguish correct code from broken code.

**T-1 Logic Coverage Score, target ≥85% (advisory, no hard gate).** Composite of branch coverage and rule-attestation; computed and emitted by tdd-python-writer at code-write time. Always pursue the highest LCS achievable; close cheapest one-or-two rule gaps each iteration. Not a CI gate.

**T-2 No vacuous assertions.** Forbidden patterns:
- `assert result is not None` (alone or as final assertion)
- `assert result` / `assertTrue(result)` on dicts or non-bool objects
- `mock.assert_called_once()` / `mock.assert_called()` without `_with(...)`
- `pytest.raises(Exception)` / `pytest.raises(BaseException)` without `match=`

**T-3 Per-branch tests.** For every `if`/`elif`/`else`, every `and`/`or` short-circuit in a predicate, every guard clause, every `try`/`except`, the test suite must exercise both the True and False outcomes at least once.

**T-4 Boundary tests.** For every numeric/ordinal parameter, parametrized rows must include `min-1, min, min+1, max-1, max, max+1`. For collections: `[]`, `[single]`, `[many]`. For nullable inputs: `None`, missing, present-but-empty.

**T-5 Negative-path tests.** Every `raise X(msg)` in production code must have a matching test using `pytest.raises(X, match=re.escape(<substring>))`. The writer must grep `^.*raise ` in the SUT and confirm a matching test exists for each.

**T-6 Authentication tests on every protected REST route.** For every FastAPI route handler:
- 401 when `Authorization` header is missing
- 401 when the token is well-shaped but expired/invalid
- 403 when the token is valid but role or org membership is wrong

Public-route exception applies only when the route has `security: []` on the operation in `openapi.json` (OpenAPI 3.1 — `security: []` means no security required). If the route isn't listed in `openapi.json`, stop and ask.

**T-7 Property-based tests (Hypothesis).** Required for any pure-function module (validators, parsers, formatters, geohash, JWT helpers). At least one `@given` test asserting an invariant (idempotence, round-trip, length-preservation, type-preservation), never an example check disguised as `@given`.

**T-8 Single-Act tests.** One Arrange, one Act (one public-method call on SUT), one focused assertion block. No `if`/`for` inside test bodies — use `@pytest.mark.parametrize` rows.

**T-9 Test names.** `test_<unit>_<scenario>_<expected>` — the name reads as the requirement.

**T-10 Mock discipline.** Mock only I/O boundaries (aioboto3, httpx, SES/SNS clients, `datetime.now`, filesystem). Do not mock internal modules.

**T-11 Mock argument verification.** Every mock-call assertion uses `assert_called_once_with(<exact kwargs>)`. A test whose only assertion is a mock-call check (no return-value or state assertion) is a Liar smell and must be rejected.

**T-12 Exception narrowing.** Always pin the narrowest exception type AND a `match=` substring.

**T-13 Decision tables.** For N independent boolean conditions in a predicate, generate up to `2^N` parametrize rows. If N > 4, the writer flags it and asks before producing 16+ rows.

**T-14 Branch coverage gate.** Project pytest config retains `--cov-branch` and `--cov-fail-under=90` as the line/branch coverage hard gate enforced in CI.

**T-15 Rule-exemption justification.** Any rule deviation must carry a `# logic-coverage-exempt: <reason>` comment naming the specific T-rule and a concrete justification (non-deterministic, third-party glue, framework boilerplate, etc.). Vague justifications are not accepted.

## Logic Coverage Score (LCS)

The Logic Coverage Score combines branch coverage (from the test runner's existing output) with an AST-driven rule attestation derived from the numbered standards (T-rules). It supersedes the previous mutation-testing approach as the rigor signal — no mutation tools required.

### Formula

```
LCS = 0.50 × branch_coverage% + 0.50 × rule_score%

rule_score = (rules_passing / rules_applicable) × 100
```

Both inputs are computed per change (changed files / functions / classes), not per whole module.

### Branch coverage source

- **Python**: `pytest --cov-branch --cov-report=json` then read `totals.percent_covered_display`.

### Decision atoms scanned (AST)

The agent walks the AST of changed source files (Python `ast` stdlib) and identifies:

- `if` / `elif` / `else` chains
- `and` / `or` short-circuits
- `try` / `except` arms
- `raise` statements (negative-path)
- `match` / `case` patterns
- numeric / collection / nullable parameters (boundary candidates)
- decision-table parameter groups (N booleans → up to 2^N rows)
- pure-function signatures (Hypothesis candidates)

### Rule attestation

For each atom found, the agent verifies a corresponding test exists by matching test-source patterns:

- **T-2** assertions are bound (no vacuous `assert x is not None` / `assert result` alone / `mock.assert_called_once()` without `_with(...)`)
- **T-3** per-branch tests exist for every branch atom
- **T-4** numeric params have min-1, min, min+1, max-1, max, max+1; collections have `[]`, `[single]`, `[many]`; nullables have `None`, missing, present-empty
- **T-5** each `raise X(msg)` has a corresponding `pytest.raises(X, match=...)` test
- **T-6** protected routes have 401 missing-auth, 401 invalid/expired, 403 wrong-role tests
- **T-7** pure functions have a `@given` Hypothesis property test asserting an invariant
- **T-8** each test has one Arrange / one Act / one assertion block (no `if`/`for` in body — use `@pytest.mark.parametrize`)
- **T-9** test names follow `test_<unit>_<scenario>_<expected>`
- **T-10** mocks target only I/O boundaries (aioboto3, httpx, SES/SNS, datetime, filesystem) — never internal modules
- **T-11** mock-call assertions use `assert_called_once_with(<exact kwargs>)` — no bare `assert_called()`
- **T-12** `pytest.raises` uses the narrowest exception class + `match=` substring
- **T-13** N booleans → up to 2^N `@pytest.mark.parametrize` rows; flag if N > 4

### Target

**≥85% LCS — advisory only, no hard gate.** Always compute and emit the score. Pursue the highest LCS achievable on every change; close the cheapest one or two rule gaps surfaced in each previous report on the next iteration. If LCS cannot reach 85%, report the score and remaining gaps for the human reviewer — do not fail or block.

CI continues to gate on the existing line/branch coverage thresholds in `pyproject.toml`. LCS is not a CI gate in this round.

### Report block (mandatory end-of-run output)

```
LOGIC COVERAGE REPORT
=====================
Scope:                   <files/functions changed>
Branch coverage:         XX.X%   (tool: pytest --cov-branch)
Rule score:              XX.X%   (Y of Z applicable rules covered)

Rule attestation:
  T-2  No vacuous assertions   COVERED      | <count> assertions, all bound
  T-3  Per-branch tests        COVERED      | 14 branch atoms, 14 covered
  T-4  Boundary tests          GAP          | param `max_items`: missing min-1 case
  T-5  Negative-path tests     COVERED      | 3 raises, 3 pytest.raises tests
  T-6  Auth tests              N/A          | no route handlers in scope
  ...

Logic Coverage Score:    XX.X%               (target: >=85%, advisory)
Gaps to close (if any):
  - T-4 boundary at <file>:<line> for param `max_items` (min-1 case)
  - ...
```

## References

- **Original PEP 8:** https://peps.python.org/pep-0008/
- **PEP 257 (Docstrings):** https://peps.python.org/pep-0257/
- **PEP 484 (Type Hints):** https://peps.python.org/pep-0484/
- **PEP 526 (Variable Annotations):** https://peps.python.org/pep-0526/
- **Hypothesis docs:** https://hypothesis.readthedocs.io/
- **OpenAPI 3.1 security object:** https://spec.openapis.org/oas/v3.1.0#security-requirement-object

---

**Document Status:** Active
**Last Updated:** May 18, 2026
**Applies To:** All Python code (applications, scripts, infrastructure)
**Authority:** PEP 8 - Style Guide for Python Code (Public Domain)
