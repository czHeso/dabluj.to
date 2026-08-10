## What does this change?

<!-- One or two sentences. What problem does it solve? -->

## Why?

<!-- Context a reviewer needs. Link the issue if there is one. -->

## How was it verified?

<!-- Which tests, and anything you checked by hand. -->

---

## Checklist

- [ ] `ruff format . && ruff check . && mypy && pytest` passes
- [ ] `npm run lint && npm run typecheck && npm run test && npm run build` passes (if the frontend changed)
- [ ] Tests added or updated — for a bug fix, a test that would have failed before
- [ ] Documentation updated in this PR if behaviour changed
- [ ] An ADR added under `docs/adr/` if this decision is expensive to reverse

### If a model was added or changed

- [ ] Its licence was **read**, not assumed
- [ ] `commercial_use` reflects what the licence actually permits
- [ ] It is listed in `docs/MODELS.md` with a link to the primary source

### Always

- [ ] No `shell=True`, and any new subprocess call passes an argument array
- [ ] No transcript text, media contents or voice samples are logged
- [ ] No feature is marked ✅ in the README unless it genuinely works
