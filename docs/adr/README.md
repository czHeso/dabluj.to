# Architecture Decision Records

Short records of decisions that were not obvious, kept so that a future reader
(including a future maintainer) can see *why* something is the way it is —
especially when the reasoning involved rejecting a popular option.

Each record states: **Status**, **Context**, **Decision**, **Alternatives
considered**, **Consequences** (both good and bad).

| # | Decision |
|---|---|
| [0001](0001-local-web-architecture.md) | A local web application, not a desktop GUI or a cloud service |
| [0002](0002-frontend-stack.md) | React + TypeScript + Vite, and no state-management library |
| [0003](0003-asr-provider.md) | faster-whisper as the default speech recognition backend |
| [0004](0004-project-format.md) | A project directory with a versioned JSON manifest, and no database |
| [0005](0005-model-management.md) | Models are never bundled, always verified, and always consented to |
| [0006](0006-job-processing.md) | An in-process job queue, not a task broker |
| [0007](0007-ffmpeg-integration.md) | FFmpeg as an external binary behind one module |
| [0008](0008-localhost-security.md) | Origin and Host checking, because localhost is not private |
| [0009](0009-python-version.md) | Python 3.10–3.12, capped by CTranslate2 |

## Adding one

Write a record when a decision is expensive to reverse, when a reasonable
reviewer would ask "why not X?", or when the answer depends on a licence or a
compatibility fact that will age. Do not write one for routine choices.

Copy the structure of an existing record. Keep it under a page.
