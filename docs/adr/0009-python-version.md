# 0009 — Python 3.10–3.12, capped by CTranslate2

**Status:** Accepted · 2026-08-10

## Context

The Python version had to be chosen from actual ML dependency compatibility,
not from "whatever is newest".

## Decision

`requires-python = ">=3.10,<3.13"`. Development and CI target **3.12**.

## Reasoning

**The upper bound is forced by CTranslate2.** It publishes neither wheels nor an
sdist for Python 3.13, so `faster-whisper` — the default ASR backend — simply
cannot be installed there. Verified 2026-08-10 against the upstream project and
its release notes.

Since this is the single hardest constraint in the dependency graph, 3.12 became
the target and 3.13 an explicit non-goal until CTranslate2 ships support.

**The lower bound is 3.10** because the codebase uses `X | Y` union syntax and
`match`-friendly structural patterns freely, and because 3.9 reaches end of life
imminently. Supporting 3.9 would mean `typing.Optional` throughout for no real
gain; 3.10 is available everywhere Dabuj targets.

Because 3.10 lacks `tomllib`, `tomli` is a conditional dependency
(`python_version < "3.11"`), and `settings.py` imports whichever is available.

## Consequences

**Good**

- The declared range is one that genuinely installs and runs.
- Modern typing syntax without `from __future__` gymnastics beyond the standard
  annotations import.
- CI tests 3.10 and 3.12 — the boundaries — rather than only the happy middle.

**Bad**

- Users on 3.13 must install a second interpreter. `pip` reports this clearly
  from `requires-python` rather than failing halfway through a build.
- The cap must be revisited when CTranslate2 ships 3.13 wheels. Loosening it is
  a one-line change plus a CI matrix entry.
