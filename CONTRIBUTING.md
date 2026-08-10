# Contributing to Dabuj

Thank you for considering it. Dabuj is early, so there is a lot of useful work
available.

## Ground rules

Two are specific to this project and matter more than the usual advice:

1. **Never claim a capability that has not been verified.** No invented
   benchmark numbers, no "supports 100 languages" without a source, no marking a
   feature ✅ in the README before it works. Status labels in the README are a
   promise to the reader.

2. **Model licences are checked, not assumed.** If you add a model to the
   catalog, read its actual licence and link it in
   [docs/MODELS.md](docs/MODELS.md). Two popular, excellent models (NLLB-200 and
   XTTS-v2) are deliberately *not* defaults because they forbid commercial use.
   Getting this wrong silently harms every user who ships something.

## Getting started

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for setup. In short:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,api,asr]"
cd frontend && npm install && cd ..
dabuj doctor
```

## Before opening a pull request

```bash
ruff format . && ruff check . && mypy && pytest
cd frontend && npm run lint && npm run typecheck && npm run test && npm run build
```

CI runs exactly this.

## What makes a good pull request

- **One thing.** A focused change is far easier to review than a sweeping one.
- **A test that would have failed before.** Especially for bug fixes.
- **Docs updated in the same PR** when behaviour changes.
- **An ADR** if the decision is expensive to reverse or a reviewer would
  reasonably ask "why not X?". See [docs/adr/](docs/adr/).

## Code conventions

- Type hints everywhere; `mypy --strict` must pass.
- Docstrings explain **why**, not what. The code already says what.
- Errors are `DabujError` subclasses with a summary, a reason and concrete
  suggestions. A user must never see only a traceback.
- No `shell=True`. Ever. Subprocess arguments are arrays.
- Never log transcript text, media contents or voice samples.
- No `NotImplementedError` or `pass` in something presented as finished. Mocks
  belong in tests, not in production paths.

## Tests

| Marker | Runs in CI |
|---|---|
| `unit` | ✅ |
| `integration` | ✅ |
| `ml` | ❌ opt-in — downloads real models |
| `gpu` | ❌ opt-in |

CI must never download gigabytes of weights. If your feature needs a real model
to test, mark it `ml` and make the test skip cleanly when the model is absent.

Generate media fixtures with FFmpeg rather than committing binaries.

## Reporting bugs

Include: what you did, what you expected, what happened, the output of
`dabuj doctor`, and the relevant part of `<data-dir>/logs/dabuj.jsonl`. Logs
contain no transcript text, so they are safe to share.

## Security

Please do **not** open a public issue for a vulnerability. See
[SECURITY.md](SECURITY.md).

## Licence

By contributing you agree that your contribution is licensed under the MIT
licence of this repository.
