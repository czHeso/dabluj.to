# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [0.1.0] — 2026-08-10

The first release: a complete, working transcription pipeline, plus the
architecture the dubbing features will be built on.

### Added

**Transcription pipeline**

- Media probing for any FFmpeg-supported container, with clear errors for files
  that contain no audio or that FFmpeg cannot read.
- Audio extraction to 16 kHz mono WAV, with real progress reporting parsed from
  FFmpeg's `-progress` stream and full cancellation support.
- Local speech recognition via faster-whisper (CTranslate2), with word-level
  timestamps, voice activity detection, and language detection that reports a
  confidence and warns when it is low.
- Five Whisper models in the catalog, from `whisper-tiny` to `whisper-large-v3`,
  all MIT-licensed.

**Transcript model**

- A non-destructive editing model: original ASR output is preserved in
  `raw_text` and never overwritten by a user edit.
- Stable segment IDs that survive editing, splitting, merging and disk
  round-trips.
- Split and merge that use word timings where available, and that correctly
  discard translations they would invalidate.

**Projects**

- A portable project directory with a schema-versioned `project.json`, written
  atomically, storing paths relative to the project so a folder can be moved.
- Migration infrastructure that refuses — rather than guesses at — a project
  written by a newer version.
- Incremental processing: a dependency graph derives what a change invalidates,
  so editing a translation never triggers a re-transcription.
- Checkpoint and resume: reopening a project after a crash resumes from the last
  completed stage.

**Model manager**

- Explicit consent before any download, showing the exact size, file count,
  source and licence.
- SHA-256 verification against the publisher's checksum, `.partial` files
  renamed only after verification, resumable transfers, and path confinement of
  every file named by a remote index.
- HTTPS verified against the operating system trust store, so Dabuj works behind
  the TLS-inspecting proxies common on corporate networks.

**Interfaces**

- A CLI (`transcribe`, `probe`, `export`, `projects`, `models`, `system-info`,
  `doctor`, `start`) that renders errors as a summary, a reason and concrete
  suggestions — never a bare traceback.
- A FastAPI backend with REST routes and a WebSocket progress feed.
- A React + TypeScript browser UI with a transcript editor, model manager, live
  processing view and system report.
- Both the CLI and the API call the same application services.

**Hardware and profiles**

- Local detection of CPU, memory, GPU, VRAM and acceleration backends, collecting
  nothing that identifies the machine.
- A deterministic, explainable profile engine that recommends Low, Balanced,
  High or Ultra and states its reasons — never from VRAM alone.

**Security and privacy**

- Origin and Host checking that blocks cross-site requests and DNS rebinding
  against the local API.
- Path confinement through a single choke point.
- Argument-array subprocess invocation throughout; `shell=True` appears nowhere.
- No telemetry, and no code to send any. Transcript text is never logged.

**Project infrastructure**

- 274 backend tests plus 5 opt-in `ml` tests that verify real transcription
  against a downloaded model, and 17 frontend tests. Markers separate unit,
  integration, `ml` and `gpu` work so CI never downloads model weights.
- Strict `mypy`, `ruff` lint and format, ESLint and TypeScript.
- GitHub Actions covering Linux, Windows and macOS on Python 3.10 and 3.12,
  including a job that guards the model licence table.
- Nine architecture decision records, and documentation covering architecture,
  models and their licences, privacy, security, development and troubleshooting.

### Known limitations

- Speaker diarization, translation, text to speech and dubbing are **not
  implemented**. See the roadmap in the README.
- The web UI takes a typed file path rather than a native file picker; the
  browser cannot supply a real filesystem path for a dropped file.
- Python 3.13 is not supported, because CTranslate2 publishes no wheels for it.

[Unreleased]: https://github.com/czHeso/dabluj.to/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/czHeso/dabluj.to/releases/tag/v0.1.0
