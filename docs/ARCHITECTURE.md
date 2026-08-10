# Architecture

## The shape of the thing

Dabuj is a **local web application**: a Python backend that serves a React
frontend to a browser on `127.0.0.1`. It is not a cloud service, and it is not a
desktop GUI toolkit application.

```
  Browser (React + TypeScript)
        │  REST + WebSocket, same origin
        ▼
  FastAPI  ──────────────┐
        │                │
        ▼                ▼
  Application services   JobManager
        │                    │
        ▼                    │
  Processing pipeline ◀──────┘
        │
        ▼
  Providers ──▶ local AI models
        │
        ▼
  FFmpeg
```

The choice is deliberate. A browser gives a capable, familiar, accessible UI
with no toolkit dependency, and the frontend/backend split means a future Tauri
wrapper needs no frontend rewrite. See
[ADR 0001](adr/0001-local-web-architecture.md).

## Layers

Dependencies point strictly inward. `domain` knows nothing about anything else.

| Layer | Package | Responsibility |
|---|---|---|
| Domain | `dabuj.domain` | Transcripts, speakers, media, languages, quality profiles. Pure data and pure functions. |
| Infrastructure | `dabuj.media`, `dabuj.models`, `dabuj.hardware`, `dabuj.projects` | FFmpeg, the model registry, machine detection, on-disk persistence. |
| Providers | `dabuj.providers` | One package per AI runtime, behind a task protocol. |
| Pipeline | `dabuj.pipeline` | Stages, cache keys, checkpoints, progress, cancellation. |
| Application | `dabuj.application` | The services the CLI and the API both call. |
| Entry points | `dabuj.cli`, `dabuj.api` | Argument parsing and HTTP. No business logic. |

### One rule above the others

**The CLI and the API call the same services.**

`dabuj transcribe movie.mkv` and `POST /api/jobs/transcribe` both end up in
`TranscriptionService`. Neither reimplements anything. When they diverge, that
is a bug.

## The transcript model

The design constraint that shapes everything downstream:

> **Original ASR output is never destroyed.**

A `Segment` keeps `raw_text` exactly as the model produced it. User edits go to
`edited_text`. `segment.text` returns the edit if there is one, otherwise the
original. So an edit is always revertable, machine and human output can always
be compared, and quality reporting can skip segments a human has already
reviewed.

Segment **IDs are stable** across editing, splitting, merging and disk
round-trips, because translations, generated audio, cache entries and quality
warnings all reference segments by ID.

## Incremental processing

Processing a film takes hours. Recomputing a stage that did not need to change
is the most expensive mistake this application can make.

Stages declare their dependencies in one table
([`stages.py`](../backend/src/dabuj/pipeline/stages.py)):

```
source → audio → asr → diarization → translation → tts → mix → mux
```

From that table, `downstream_of()` derives what a change invalidates. Editing a
translation invalidates TTS, mixing and muxing — and *not* recognition or
diarization. That property is asserted directly in the test suite, because it is
the difference between a five-minute re-dub and a forty-minute re-transcription.

### Cache keys

A stage's output is reusable exactly when its inputs are unchanged. A cache key
hashes:

- fingerprints of upstream artefacts,
- the model ID and its pinned revision,
- the provider name and version,
- the settings that affect the result.

Large files are fingerprinted by `(size, mtime_ns)`, not by hashing contents —
hashing a 12 GB source on every run would cost more than the stage being cached.
Paths are reduced to filenames, so **moving a project does not invalidate its
cache**.

### Checkpoints and resume

Each stage records its state and cache key in `project.json`. On reopening, a
stage is skipped only if it completed, its recomputed key still matches, *and*
its artefact is still on disk. The third condition matters because users delete
cache folders to reclaim space.

## Jobs

A single-user desktop application does not need Redis, Celery, or a message
broker, and Dabuj has none. It has a queue, a worker thread and a dictionary.

What it does get right:

- Work runs **off the request thread** — a multi-hour job cannot live in an HTTP
  request or a `BackgroundTasks` callback.
- **Serial by default.** Two jobs sharing one GPU exhaust its memory. Concurrency
  is a parameter, not a constant, so CPU-only stages can overlap later.
- **Cancellable.** Cancelling a queued job means it never starts.
- **A failing job never kills the worker.**

## Cancellation

Cooperative. A `CancellationToken` is a thread-safe flag plus a callback list;
stages call `raise_if_cancelled()` at points where stopping is safe, and
subprocess-based stages register a callback that terminates the child.

Cancellation must leave the project valid: completed checkpoints survive, partial
files are removed. `.partial` files that are renamed only on success are what
make that true throughout — for FFmpeg output, model downloads, exports and the
project manifest alike.

## Errors

Every deliberate failure is a `DabujError` carrying three things:

- `summary` — one sentence, plain language,
- `reason` — why,
- `suggestions` — what to do about it.

`to_payload()` produces the structure the CLI panel and the HTTP error handler
both render, so the two surfaces cannot drift. Tracebacks go to the log file. A
user never sees one unless they pass `--debug`.

## Progress

One `ProgressEvent` shape serves the CLI progress bar and the WebSocket feed.

**On ETA:** Dabuj shows an estimate only once it has evidence for one — at least
5% of a stage done and 5 seconds elapsed. A number swinging between "3 minutes"
and "2 hours" is worse than no number.

Progress events deliberately carry **no transcript text**, so content cannot
leak into logs or browser consoles.

## Providers

Each task is a protocol; each provider encapsulates exactly one runtime.

```
ASRProvider
 └── FasterWhisperProvider   (CTranslate2)
```

Rules:

- Runtimes are imported **lazily**, inside `load()`. Listing the model catalog on
  a machine with no ML dependencies costs nothing and fails nowhere.
- A provider reports capabilities *before* being asked to work, so impossible
  requests fail immediately with a clear message rather than halfway through a
  two-hour job.
- **No silent fallback.** An explicit `--device cuda` on a machine without CUDA
  is an error, not a quiet downgrade to CPU. Quietly turning twenty minutes into
  six hours without saying so is worse than failing.

There is no `if provider_name == ...` anywhere outside `dabuj.providers`.

## Security

Binding to `127.0.0.1` stops other machines. It does not stop the user's own
browser, so `dabuj.api.security` blocks two further attacks:

- **Cross-site requests** — any request carrying a foreign `Origin` is rejected
  before routing. CORS alone is insufficient: simple requests are not
  preflighted, so CORS only stops the attacker *reading* the reply, which is no
  help when the request itself is the damage.
- **DNS rebinding** — a request whose `Host` is not a loopback name is rejected.

`Access-Control-Allow-Origin: *` appears nowhere. See
[SECURITY.md](SECURITY.md).

## Storage

```
<data-dir>/
├── dabuj.toml        user settings
├── models/           downloaded weights (never in git)
├── projects/<id>/
│   ├── project.json  versioned manifest
│   ├── source/
│   ├── cache/        per-stage artefacts
│   └── exports/
└── logs/dabuj.jsonl
```

Nothing is ever written into the source tree. Project paths are stored
**relative** to the project directory, so a project folder can be moved, copied
or synced and still open.

`resolve_within()` is the single choke point turning untrusted input into a
filesystem path; everything user-facing goes through it.

## Why no database

Project data lives in `project.json` next to the media it describes: it is
portable, diffable, and survives the application being uninstalled. A database
would add a dependency and a migration surface to a single-user tool that has
no concurrent writers. If cross-project indexing ever becomes slow, SQLite is
the answer for the *index*, not for the project contents. See
[ADR 0004](adr/0004-project-format.md).

## Testing

| Marker | What it covers | In CI |
|---|---|---|
| `unit` | Pure logic. No binaries, no network. | ✅ Always |
| `integration` | Real FFmpeg, real filesystem, the API. | ✅ Always |
| `ml` | Downloads and runs a real model. | ❌ Opt-in only |
| `gpu` | Needs a GPU. | ❌ Opt-in only |

CI never downloads gigabytes of weights. The pipeline's orchestration is tested
against a fake ASR provider that counts its own invocations, so a test can prove
caching *prevented work* rather than merely that the output looked right.
