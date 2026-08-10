# Development

## Setup

**Prerequisites:** Python 3.10–3.12 (3.12 recommended), Node 20+, FFmpeg.

Python 3.13 will not work: CTranslate2 publishes no wheels for it. See
[ADR 0009](adr/0009-python-version.md).

```bash
git clone https://github.com/czHeso/dabluj.to.git
cd dabluj.to

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e ".[dev,api,asr]"

cd frontend && npm install && cd ..

dabuj doctor
```

## Running

```bash
# Backend only, with the built frontend if one exists
dabuj start

# Frontend with hot reload, proxying the API to the backend
cd frontend && npm run dev        # http://localhost:5173
```

The Vite dev server proxies `/api` and `/ws` to `127.0.0.1:7860`, and `:5173` is
in the API's origin allow-list, so no CORS special-casing is needed.

## Tests

```bash
pytest                     # unit + integration (the default)
pytest -m unit             # fast, hermetic, no binaries
pytest -m integration      # real FFmpeg, real filesystem, the API
pytest -m ml               # downloads and runs real models — opt-in
pytest --cov=dabuj
```

| Marker | Meaning |
|---|---|
| `unit` | Pure logic. No subprocesses, no network, no models. |
| `integration` | Needs FFmpeg or exercises the API/filesystem. |
| `ml` | Downloads or runs a real AI model. Never in normal CI. |
| `gpu` | Requires a GPU. |
| `slow` | More than a few seconds. |

Integration tests **generate** their media fixtures with FFmpeg rather than
committing binaries — no licensing questions, no repository bloat, and the
fixture is always exactly what the test expects.

The pipeline is tested against a **fake ASR provider that counts its own
invocations**, so a caching test can prove work was actually *prevented* rather
than merely that the output looked right.

### Frontend

```bash
cd frontend
npm run test          # vitest
npm run typecheck
npm run lint
npm run build
```

## Code quality

```bash
ruff format .          # format
ruff check . --fix     # lint
mypy                   # strict type checking
```

All three must pass. `mypy` runs in strict mode over the whole backend.

## Layout

```
backend/src/dabuj/
├── domain/        pure data: transcripts, speakers, media, languages
├── media/         the FFmpeg boundary
├── hardware/      detection + the profile recommendation engine
├── models/        catalog, registry, downloader
├── providers/     one package per AI runtime, behind a protocol
├── pipeline/      stages, cache, checkpoints, progress, cancellation
├── projects/      on-disk format and migrations
├── export/        SRT, VTT, JSON, TXT
├── jobs/          the local queue
├── application/   services shared by CLI and API
├── api/           FastAPI routes, WebSocket, security
└── cli/           Typer commands
```

Dependencies point inward. `domain` imports nothing from the layers above it.

## Conventions

- **Type hints everywhere**; `mypy --strict` must pass.
- **Docstrings explain *why***. What the code does is visible; why it does it
  that way is not.
- **Errors are `DabujError` subclasses** with a summary, a reason and
  suggestions. A user must never see only a traceback.
- **New processing stages** are added to `pipeline/stages.py` *with their
  dependencies*, so cache invalidation stays correct by construction.
- **New providers** import their runtime lazily inside `load()` and encapsulate
  it completely. No `if provider_name == ...` outside `dabuj.providers`.

## Adding a provider

1. Implement the task protocol (e.g. `ASRProvider` in
   `providers/asr/base.py`).
2. Register a factory in `providers/registry.py`.
3. Add the model(s) to `models/catalog.py` **with a verified license**.
4. Document them in [MODELS.md](MODELS.md) with a link to the primary source.

The provider's runtime must be imported inside `load()`, never at module import,
so that listing the catalog works on a machine with no ML dependencies.

## Adding a pipeline stage

1. Add it to `Stage` and to `STAGE_ORDER`.
2. Declare its **direct** dependencies in `STAGE_DEPENDENCIES` — transitive ones
   are derived.
3. Add a cache category in `projects/store.py` if it writes artefacts.
4. Add a test asserting what changing it invalidates. This is the property that
   keeps a translation edit from triggering a re-transcription.

## Before opening a pull request

```bash
ruff format . && ruff check . && mypy && pytest
cd frontend && npm run lint && npm run typecheck && npm run test && npm run build
```

CI runs exactly this. It does **not** run `ml` or `gpu` tests, and it never
downloads model weights.
