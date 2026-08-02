# Simple Code Standards

**Build for what the task needs now — not a speculative future.**

This document is a **language-neutral** standard for code simplicity. It applies to
every language in the workspace (Python, TypeScript/JavaScript, Kotlin, Swift, C#,
Shell, infrastructure code, and config). Read it before designing, writing, or
editing any source file, alongside the language-specific standard.

It exists because generated code — including AI/LLM-generated code — reliably
*over-complicates*: it adds abstraction layers nobody asked for, wraps simple logic in
defensive scaffolding, splits one cohesive idea across too many files, and hands you a
"house of cards" that is hard to read and harder to change. Empirical studies of LLM
output confirm the pattern: >90% of detected issues are maintainability code smells
(verbosity, dead code, duplication), duplicated code blocks have risen ~8x, and more
"capable" models tend to emit *more* complex code, not cleaner code. The rules below
are the counter-pressure.

## Core Philosophy

> "Simple is better than complex." — The Zen of Python (PEP 20)

> "Duplication is far cheaper than the wrong abstraction." — Sandi Metz

> "Always implement things when you actually need them, never when you just foresee
> that you need them." — Ron Jeffries (YAGNI)

> "Premature optimization is the root of all evil." — Donald Knuth

**The contract:**

- **Write the smallest thing that fully solves the actual requirement.** Not the most
  general thing, not the most future-proof thing — the simplest thing that is correct,
  clear, and complete for what was asked.
- **Code is read far more often than it is written.** Optimize for the next person
  (often you) reading it cold.
- **Simplicity is a requirement, not a nicety.** Over-complication is a defect to be
  flagged in review, the same as a bug.

**Scale-readiness is not over-engineering.** A pattern that a present, written
requirement demands — e.g. connection pooling, pagination, or a query shape this
project's performance standard mandates — is *essential* complexity and stays. The
target of this standard is *accidental* complexity: structure added for needs no task
or standard actually has yet. "Don't build for three years from now" means *don't add
speculative features and abstractions*; it does **not** mean skip the patterns your
current requirements already call for.

---

## The Rules

### 1. YAGNI — don't build for a future you only imagine

Implement only what the current task requires. No speculative parameters, config
options, hooks, plugin systems, "extensibility points," or unrequested features added
"while we're here." Every speculative element has a build cost, a carry cost (it drags
on all future work), and a repair cost when the guess turns out wrong.

```
# Wrong — asked for "save a user", delivered a framework
def save_user(user, *, retries=3, backend="dynamo", on_conflict="merge",
              audit=True, dry_run=False, serializer=None, cache_ttl=None):
    ...

# Correct — solve the actual task
def save_user(user):
    ...
```

If a parameter, branch, class, or flag has no current caller that needs it, delete it.

### 2. KISS — choose the simplest construct that works

Prefer the plainest mechanism that solves the problem: a function over a class, a
literal over a builder, a list over a custom collection, a direct call over a
dispatch table. Reach for a heavier construct only when the simple one genuinely fails.

### 3. Rule of three — don't abstract until the third real duplication

Two similar pieces of code are fine. Extract a shared abstraction on the **third** real
occurrence, when the true shape is visible. Abstracting on the first or second guess
crystallizes a misunderstanding into structure. If an existing abstraction has started
to accrete flags and conditionals to serve divergent callers, **inline it back** and
let the duplication reveal the right seam ("the fastest way forward is back").

### 4. No single-implementation interfaces or premature subclasses

Do not create an interface / abstract base / protocol that has exactly one
implementation, or a subclass hierarchy with one concrete subclass. Add the interface
only when there is a second real implementation, or when it genuinely cuts a
problematic cross-module dependency. Refactor *to* interfaces; don't program *to* them
speculatively.

```
// Wrong — one interface, one impl, no second implementer in sight
interface UserRepository { save(u): void }
class UserRepositoryImpl implements UserRepository { save(u) { ... } }

// Correct — just the class until a second implementation actually exists
class UserRepository { save(u) { ... } }
```

### 5. Prefer a function over a class when there is no state

If a "class" holds no persistent state — it's just `__init__` plus one method, or a
bag of static methods — it should be a function (or a small module of functions).
Classes earn their keep by bundling state with the behavior that operates on it.

### 6. Collapse needless indirection

Delete pass-through / "middle man" wrappers that only forward a call to something
else without adding behavior. Each hop a reader must follow to find the real logic is
a tax. Use only the layers you actually need — no "lasagna" of controller → helper →
service → mapper → manager for a one-line read.

### 7. One file/module until there is a concrete reason to split

Do not scatter a cohesive unit across many tiny files. Prefer one **deep** module
(simple interface, substantial implementation) over many **shallow** ones (interface
nearly as large as the functionality). Split when a file has genuinely separable
responsibilities or has grown hard to navigate — not by reflex, and not one-class-per-
file as dogma.

### 8. Keep nesting shallow — use guard clauses and early returns

Flatten "arrow code." Handle special cases up front with early returns so the normal
path is unindented and obvious. **Cap block-nesting at ~4 levels.**

```
# Wrong — arrow code
def process(order):
    if order:
        if order.is_paid:
            if order.items:
                return ship(order)
    return None

# Correct — guard clauses
def process(order):
    if not order or not order.is_paid or not order.items:
        return None
    return ship(order)
```

### 9. Delete dead code, unused flags, and unreachable config

No commented-out blocks, unused parameters, unreferenced config keys, "just in case"
branches, or feature flags with one permanent value. Version control is your history;
the working tree is for live code only.

### 10. Keep functions short and single-purpose

A function should do one thing at one level of abstraction. **Guidance:** keep
functions to roughly ≤ 50 lines, **cyclomatic complexity ≤ 10**, **cognitive
complexity ≤ 15**, and **≤ 3 parameters** (bundle more into a parameter object). These
are review signals, not laws — but a function that blows past several at once is a
refactor candidate.

### 11. No premature optimization

Write the clear version first. Optimize only the specific hot path you have *measured*.
Speculative caching, micro-optimizations, and hand-rolled "fast" code that obscure
intent without proven need are over-complication. (This does not waive performance
patterns your project's standards already require for known scale — those are
requirements, not speculation; see "Scale-readiness" above.)

### 12. Comments explain *why*, never restate the *what*

Delete comments that narrate obvious code. Keep comments that capture intent,
trade-offs, non-obvious constraints, or links to context. Self-documenting names beat
a comment that patches a bad one. Excessive explanatory comments are a known AI smell —
they raise reading cost, not lower it.

### 13. Use the smallest data structure that fits

Don't model fields you don't use. Don't wrap a value in a record/dataclass/enum when a
plain value, tuple, or dict suffices; don't reach for a custom class when a built-in
collection works. Match the structure to the data you actually have today.

### 14. Match existing patterns — don't import generic "textbook" structure

Follow the conventions, naming, and architecture already in this codebase. Generated
code tends to emit context-blind boilerplate (repository/factory/service layers,
enterprise scaffolding) that doesn't fit. Coherence with the surrounding code is part
of simplicity.

### 15. Fail loudly; don't over-wrap in defensive scaffolding

Don't blanket code in catch-all error handlers, silent fallbacks, defaults on required
inputs, or "just in case" type coercion. Validate real boundaries; let invariant
violations surface. (This complements — does not override — your language standard's
specific error-handling rules, e.g. "no bare except.")

---

## Quick decision heuristics

Before adding a layer, ask:

- **"Is there a real, present caller/requirement for this?"** No → don't add it (Rule 1).
- **"Have I actually duplicated this three times?"** No → don't extract it yet (Rule 3).
- **"Does this interface/class/file earn its existence?"** A second implementation? Real
  state? A separable responsibility? No → collapse it (Rules 4–7).
- **"Could the next reader follow this without jumping through indirection?"** No →
  flatten it (Rules 6, 8).
- **"Am I solving the task, or a more general problem nobody asked about?"** The
  general one → stop (Rules 1, 2).

---

## Review checklist

- [ ] Every parameter, branch, class, flag, and file has a present reason to exist.
- [ ] No interface/abstract/subclass with a single implementation.
- [ ] No stateless class that should be a function.
- [ ] No pass-through/middle-man indirection or unnecessary layering.
- [ ] Nesting ≤ ~4 levels; special cases handled with guard clauses.
- [ ] Functions short and single-purpose (≈ ≤ 50 lines, CC ≤ 10, cognitive ≤ 15, ≤ 3 params).
- [ ] No dead code, commented-out blocks, or unused config.
- [ ] No speculative generality / "future-proofing" beyond current requirements.
- [ ] No premature optimization or speculative caching (measured hot paths only).
- [ ] Comments explain *why*, not *what*; no narration of obvious code.
- [ ] Smallest data structure that fits the data you actually have.
- [ ] Code matches existing codebase conventions, not generic textbook scaffolding.
- [ ] No catch-all defensive wrapping that hides real failures.

---

## When NOT to apply these rules

Simplicity is the goal, not minimalism-at-all-costs. Do **not**:

- Compress code into clever one-liners or dense expressions that are harder to read —
  that trades one form of complexity for another.
- Remove an abstraction that genuinely organizes real, present complexity or that an
  existing requirement mandates.
- Strip out essential complexity inherent to the problem, or required scale/performance
  patterns the project's standards already demand.
- Inline something so aggressively that a real, repeated concern becomes scattered.

The test for every element is the same: **does it earn its place against a present
need?** Keep what does; delete what doesn't.

---

## Sources

This standard is grounded in the following references (≥ 50 sources reviewed).

**AI / LLM over-complication**

- Complacency with AI-generated code — Thoughtworks Technology Radar — https://www.thoughtworks.com/radar/techniques/complacency-with-ai-generated-code
- Investigating The Smells of LLM Generated Code (arXiv 2510.03029) — https://arxiv.org/pdf/2510.03029
- How Propense Are LLMs at Producing Code Smells? (arXiv 2412.18989) — https://arxiv.org/html/2412.18989v1
- Assessing the Quality and Security of AI-Generated Code (arXiv 2508.14727) — https://arxiv.org/html/2508.14727v1
- The Coding Personalities of Leading LLMs — SonarSource — https://www.sonarsource.com/company/press-releases/the-coding-personalities-of-leading-llms/
- How AI-generated code compounds technical debt — LeadDev — https://leaddev.com/technical-direction/how-ai-generated-code-accelerates-technical-debt
- AI-Generated Code Creates New Wave of Technical Debt — InfoQ — https://www.infoq.com/news/2025/11/ai-code-technical-debt/
- Defensive Code, Dangerous Data: The Hidden Bias of AI Coding Assistants — https://medium.com/data-mess/defensive-code-dangerous-data-the-hidden-bias-of-ai-coding-assistants-2336179ff51b
- PR Slop: The Quality Crisis in AI-Generated Pull Requests — https://asdlc.io/concepts/pr-slop/
- How to Avoid AI Code Slop — Aviator — https://www.aviator.co/blog/how-to-avoid-ai-code-slop/
- YAGNI — Encyclopedia of Agentic Coding Patterns — https://aipatternbook.com/yagni
- AI's 70% Problem (Addy Osmani) — Zed Blog — https://zed.dev/blog/ai-70-problem-addy-osmani
- Generative AI is not going to build your engineering team for you — Stack Overflow Blog — https://stackoverflow.blog/2024/12/31/generative-ai-is-not-going-to-build-your-engineering-team-for-you/
- The Challenges of Producing Quality Code When Using AI-Based Generalistic Models — InfoQ — https://www.infoq.com/news/2023/10/producing-quality-code-AI/

**Timeless over-engineering: YAGNI, KISS, simplicity**

- Yagni — Martin Fowler — https://martinfowler.com/bliki/Yagni.html
- You aren't gonna need it — Wikipedia — https://en.wikipedia.org/wiki/You_aren%27t_gonna_need_it
- The Wrong Abstraction — Sandi Metz — https://sandimetz.com/blog/2016/1/20/the-wrong-abstraction
- Simple Made Easy (Rich Hickey, transcript) — https://github.com/matthiasn/talk-transcripts/blob/master/Hickey_Rich/SimpleMadeEasy.md
- A Philosophy of Software Design (Ousterhout) — notes — https://sive.rs/book/PoSD
- No Silver Bullet — Wikipedia — https://en.wikipedia.org/wiki/No_Silver_Bullet
- Speculative Generality — Refactoring Guru — https://refactoring.guru/smells/speculative-generality
- Beck Design Rules — Martin Fowler — https://martinfowler.com/bliki/BeckDesignRules.html
- Don't repeat yourself — Wikipedia — https://en.wikipedia.org/wiki/Don%27t_repeat_yourself
- Gold Plating — Coding Horror (Jeff Atwood) — https://blog.codinghorror.com/gold-plating/
- Gold Plating — The Daily Software Anti-Pattern — https://exceptionnotfound.net/gold-plating-the-daily-software-anti-pattern/
- KISS principle — Wikipedia — https://en.wikipedia.org/wiki/KISS_principle
- Donald Knuth on premature optimization — Wikiquote — https://en.wikiquote.org/wiki/Donald_Knuth
- Simple Design — DevIQ — https://deviq.com/practices/simple-design/

**Over-modularization, layering, and wrong abstractions**

- A Philosophy of Software Design: My Take — Pragmatic Engineer — https://blog.pragmaticengineer.com/a-philosophy-of-software-design-review/
- A Philosophy of Software Design (overview) — https://medium.com/swlh/a-philosophy-of-software-design-by-john-ousterhout-4a00d0ff9f1c
- Middle Man — Refactoring Guru — https://refactoring.guru/smells/middle-man
- Abstraction: The Rule Of Three — Derick Bailey — https://lostechies.com/derickbailey/2012/10/31/abstraction-the-rule-of-three/
- Rule of three (computer programming) — Wikipedia — https://en.wikipedia.org/wiki/Rule_of_three_(computer_programming)
- The Programming to Interfaces Anti-Pattern — https://rrees.me/2009/01/31/programming-to-interfaces-anti-pattern/
- Why an interface with only one implementation? — Ted Kaminski — https://www.tedinski.com/2018/07/31/interfaces-cutting-dependencies.html
- Lasagna code — too many layers? — Matthias Noback — https://matthiasnoback.nl/2018/02/lasagna-code-too-many-layers/
- Don't Let Architecture Astronauts Scare You — Joel Spolsky — https://www.joelonsoftware.com/2001/04/21/dont-let-architecture-astronauts-scare-you/
- Singleton — Game Programming Patterns (Robert Nystrom) — https://gameprogrammingpatterns.com/singleton.html
- When does Dependency Injection become an anti-pattern? — https://davidscode.com/blog/2015/04/17/when-does-dependency-injection-become-an-anti-pattern/
- You Don't Need Microservices (Yet) — https://dev.to/gavincettolo/you-dont-need-microservices-yet-a-reality-check-for-devs-54ec

**Complexity metrics & readability**

- Cognitive Complexity (white paper) — SonarSource — https://www.sonarsource.com/resources/cognitive-complexity/
- Cognitive Complexity rule S3776 — SonarQube — https://next.sonarqube.com/sonarqube/coding_rules?open=java%3AS3776&rule_key=java%3AS3776
- Cyclomatic complexity (McCabe) — Wikipedia — https://en.wikipedia.org/wiki/Cyclomatic_complexity
- An Empirical Validation of Cognitive Complexity (arXiv 2007.12520) — https://arxiv.org/abs/2007.12520
- Cyclomatic vs Cognitive Complexity for understandability (arXiv 2303.07722) — https://arxiv.org/abs/2303.07722v1
- complexity — ESLint — https://eslint.org/docs/latest/rules/complexity
- max-depth — ESLint — https://eslint.org/docs/latest/rules/max-depth
- max-lines-per-function — ESLint — https://eslint.org/docs/latest/rules/max-lines-per-function
- max-nested-callbacks — ESLint — https://eslint.org/docs/latest/rules/max-nested-callbacks
- complex-structure (C901) — Ruff — https://docs.astral.sh/ruff/rules/complex-structure/
- Introduction to Code Metrics — Radon — https://radon.readthedocs.io/en/latest/intro.html
- Replace Nested Conditional with Guard Clauses — Refactoring catalog (Fowler) — https://refactoring.com/catalog/replaceNestedConditionalWithGuardClauses.html
- Clean Code, Ch. 3 "Functions" (Robert C. Martin) — InformIT — https://www.informit.com/articles/article.aspx?p=1375308
- What to look for in a code review — Google Engineering Practices — https://google.github.io/eng-practices/review/reviewer/looking-for.html
- PEP 8 – Style Guide for Python Code — https://peps.python.org/pep-0008/

**Language-specific over-complication (Python exemplars; principles generalize)**

- PEP 20 – The Zen of Python — https://peps.python.org/pep-0020/
- Stop Writing Classes (Jack Diederich, PyCon 2012) — https://pyvideo.org/pycon-us-2012/stop-writing-classes.html
- Method could be a function — Python Anti-Patterns — https://docs.quantifiedcode.com/python-anti-patterns/correctness/method_could_be_a_function.html
- Implementing Java-style getters and setters — Python Anti-Patterns — https://docs.quantifiedcode.com/python-anti-patterns/correctness/implementing_java-style_getters_and_setters.html
- too-many-nested-blocks (PLR1702) — Ruff — https://docs.astral.sh/ruff/rules/too-many-nested-blocks/
- PEP 760 – No More Bare Excepts — https://peps.python.org/pep-0760/
- The Most Diabolical Python Antipattern — Real Python — https://realpython.com/the-most-diabolical-python-antipattern/
- Common anti-patterns in Python — DeepSource — https://deepsource.com/blog/8-new-python-antipatterns
- The Composition Over Inheritance Principle — Brandon Rhodes — https://python-patterns.guide/gang-of-four/composition-over-inheritance/
- Getters and Setters: Manage Attributes in Python — Real Python — https://realpython.com/python-getter-setter/
- Python Metaclasses — Real Python — https://realpython.com/python-metaclasses/
- Should we always use dataclasses? — discuss.python.org — https://discuss.python.org/t/should-we-always-use-dataclasses/16660
- How to Use Python dataclasses the Right Way (and When Not To) — https://medium.com/the-pythonworld/how-to-use-python-dataclasses-the-right-way-and-when-not-to-713d82368655
- Python asyncio: why your async code is slow — https://wittycoder.in/blog/python-asyncio-guide-2026
- Beyond PEP 8 (Raymond Hettinger, PyCon 2015) — Caktus Group — https://www.caktusgroup.com/blog/2015/05/05/pycon-2015-must-see-talk-beyond-pep-8-raymond-hettinger-26/

---

**Document Status:** Active
**Last Updated:** June 5, 2026
**Applies To:** All source code, all languages (read alongside the language-specific standard)
**Authority:** Synthesis of KISS, YAGNI, the Rule of Three, Beck's Rules of Simple Design, Ousterhout's *A Philosophy of Software Design*, and the ≥50 sources above
