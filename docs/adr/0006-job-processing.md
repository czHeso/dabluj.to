# 0006 — An in-process job queue, not a task broker

**Status:** Accepted · 2026-08-10

## Context

Transcribing a feature film can take hours. That work cannot happen inside an
HTTP request. It must be cancellable, observable, and survive a browser being
closed. But Dabuj is a single-user desktop application.

## Decision

A **`JobManager`**: a `queue.Queue`, worker threads, and a dictionary of jobs.
**Serial execution by default** (`concurrency=1`).

Explicitly rejected: Redis, Celery, RQ, Kafka, Kubernetes, or any broker.

## Alternatives considered

**FastAPI `BackgroundTasks`.** The obvious reach. Rejected: it is tied to the
request lifecycle, offers no cancellation, no progress, no queue and no
introspection. Fine for sending an email, wholly unsuited to a four-hour job.

**Celery + Redis.** Robust and well understood — and completely wrong here. It
would make the user install and run a Redis server to transcribe a video on
their own laptop. The prompt's constraint and plain good sense agree.

**`multiprocessing`.** Would side-step the GIL. Rejected because the heavy work
already happens in CTranslate2 and FFmpeg, both of which release the GIL, and
because passing large results across a process boundary and terminating
subprocesses cleanly is materially more complex than threads.

## Why serial by default

Two jobs sharing one GPU exhaust its VRAM and both fail. Concurrency is a
constructor parameter rather than a constant, so CPU-only stages can safely
overlap once the manager understands resource classes.

## Consequences

**Good**

- Zero infrastructure: `pip install` and run.
- Cancelling a queued job means it never starts.
- A failing job is recorded and the worker survives — asserted by test.
- The same `ProgressReporter` feeds the CLI bar and the WebSocket.

**Bad**

- Jobs do not survive restarting the application. Mitigated by pipeline
  checkpoints: a re-run resumes from the last completed stage rather than
  starting over, which recovers the expensive part.
- Job history is in memory and pruned at 100 entries.
- Threads share one process, so a hard crash in native code takes everything
  down. Accepted: that is equally true of a single-process desktop app.
