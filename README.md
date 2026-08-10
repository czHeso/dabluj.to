<div align="center">

# Dabuj

**A local-first AI transcription, translation and dubbing studio.**

Your media stays on your computer.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10–3.12](https://img.shields.io/badge/Python-3.10%E2%80%933.12-3776AB.svg)](pyproject.toml)
[![Status: alpha](https://img.shields.io/badge/Status-alpha-orange.svg)](#current-status)

</div>

---

## Overview

Dabuj transcribes speech, translates it, and — eventually — re-voices it with
synthetic speakers, producing a dubbed audio or video file. It runs as a small
local web application: you start it from a terminal, your browser opens, and
the processing happens on your own CPU or GPU.

It is not a cloud service and it is not a wrapper around one. When every
selected provider is a local one, nothing about your media leaves the machine —
no uploads, no API calls, no telemetry.

## Why this project?

Commercial AI dubbing services require you to upload your media to someone
else's computer. That is a poor fit for unreleased footage, confidential
interviews, medical or legal recordings, client work under NDA, or simply
anything you would rather keep private.

Dabuj exists to make that upload unnecessary.

## Current status

Dabuj is at **v0.1.0** and is **alpha software**. The transcription vertical
slice works end to end; everything after it is scaffolded but not yet built.

| Capability | Status |
|---|---|
| Media probing (any FFmpeg-supported container) | ✅ Implemented |
| Audio extraction and conversion | ✅ Implemented |
| Local speech recognition (faster-whisper) | ✅ Implemented |
| Automatic language detection with confidence | ✅ Implemented |
| Word-level timestamps | ✅ Implemented |
| Timestamped, speaker-aware transcript model | ✅ Implemented |
| Project format with schema versioning | ✅ Implemented |
| Export: JSON, SRT, WebVTT, TXT | ✅ Implemented |
| Model manager (download, verify, remove) | ✅ Implemented |
| Hardware detection and profile recommendation | ✅ Implemented |
| CLI (`transcribe`, `models`, `doctor`, `system-info`) | ✅ Implemented |
| Incremental cache and checkpoint/resume | ✅ Implemented |
| Local web UI + REST/WebSocket API | 🧪 Experimental |
| Speaker diarization | 🗺 Planned (v0.3) |
| Local translation + glossary | 🗺 Planned (v0.4) |
| Text to speech and voice mapping | 🗺 Planned (v0.5) |
| Multi-speaker dubbing and duration matching | 🗺 Planned (v0.6) |
| Audio mixing and video muxing | 🗺 Planned (v0.7) |
| Background/music preservation via source separation | 🗺 Planned (v0.8) |

Nothing in this README describes functionality that does not exist. Anything
marked 🗺 is a plan, not a promise.

## How it works

```
  Browser UI  ──REST/WebSocket──▶  FastAPI backend
                                          │
                                          ▼
                                  Processing core
                    (ASR · diarization · translation · TTS · mixing)
                                          │
                                          ▼
                          Local AI models  +  FFmpeg
```

The CLI and the web UI call the *same* application services. There is no
business logic in the frontend and no duplicated pipeline behind the CLI.

## Privacy

- Dabuj binds to `127.0.0.1` only. It is not reachable from your network unless
  you explicitly enable that.
- **Telemetry is off, and there is no code to send any.**
- Transcript text and media contents are never written to logs.
- Cloud providers are opt-in behind a master switch that is off by default.
  There is no silent cloud fallback: if a local model fails, Dabuj tells you.

See [docs/PRIVACY.md](docs/PRIVACY.md).

## Supported languages

Dabuj is tested against **English**, **German** and **Czech**, but the language
list is not hard-coded. It is read from the capabilities of whichever model you
have selected, so installing a model with wider coverage widens the list in the
UI. Dabuj will not offer a language the active model does not actually support.

## Hardware profiles

| Profile | Target machine | Approach |
|---|---|---|
| **Low** | Office laptop, no discrete GPU | Quantised (int8) models on CPU |
| **Balanced** | Modern CPU, 16 GB RAM, 4–8 GB VRAM | Good quality at reasonable speed |
| **High** | 8–12 GB VRAM, 32 GB RAM | Larger models, better accuracy |
| **Ultra** | Workstation | Best available, quality over speed |

Profiles are **recommendations, not constraints**. Every individual setting —
model, runtime, device, precision, quantisation, beam size — can be overridden.
Dabuj never assumes CUDA and is genuinely usable CPU-only.

## Installation

**Prerequisites:** Python 3.10–3.12 and FFmpeg.

```bash
# Windows
winget install Python.Python.3.12
winget install Gyan.FFmpeg

# macOS
brew install python@3.12 ffmpeg

# Debian / Ubuntu
sudo apt install python3.12 python3.12-venv ffmpeg
```

Then:

```bash
git clone https://github.com/czHeso/dabluj.to.git
cd dabluj.to

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -e ".[all]"
```

Verify the install:

```bash
dabuj doctor
```

## Quick start

```bash
# What is in this file?
dabuj probe interview.mkv

# Transcribe it. The model is downloaded on first use, after asking.
dabuj transcribe interview.mkv --language auto

# Pick a specific model and force CPU
dabuj transcribe interview.mkv --model whisper-small-int8 --device cpu

# Export
dabuj export <project-id> --format srt --output subtitles.srt
```

The first run will offer to download an ASR model and tell you exactly how
large it is. Dabuj never downloads a multi-gigabyte model without asking.

## Using the web UI

```bash
dabuj start
```

This starts the local server on `http://127.0.0.1:7860` and opens your browser.
If the port is taken, Dabuj picks the next free one.

## CLI

| Command | Purpose |
|---|---|
| `dabuj start` | Start the local web application |
| `dabuj probe <file>` | Show what a media file contains |
| `dabuj transcribe <file>` | Transcribe to a project |
| `dabuj export <project>` | Export SRT / VTT / JSON / TXT |
| `dabuj projects list` | List local projects |
| `dabuj models list` | Show available and installed models |
| `dabuj models install <id>` | Download a model |
| `dabuj models remove <id>` | Delete a model |
| `dabuj system-info` | Hardware report and recommended profile |
| `dabuj doctor` | Diagnose the installation |

Run `dabuj --help` or `dabuj <command> --help` for full options.

## Models

Dabuj ships **no model weights**. Models are downloaded on request into your
application data directory and can be removed at any time.

> **The MIT license of this repository covers the source code only. Downloaded
> AI models carry their own licenses, some of which restrict commercial use.**

Every model Dabuj can install, with its license, size and verified source, is
listed in [docs/MODELS.md](docs/MODELS.md).

## Architecture

```
backend/src/dabuj/
├── domain/        Pure data model: transcripts, speakers, media, languages
├── media/         The FFmpeg boundary — all media I/O goes through here
├── hardware/      Machine detection and the profile recommendation engine
├── models/        Model catalog, registry and secure downloader
├── providers/     Pluggable ASR / diarization / translation / TTS backends
├── pipeline/      Stages, cache keys, checkpoints, progress, cancellation
├── projects/      On-disk project format and schema migrations
├── export/        SRT, WebVTT, JSON, TXT writers
├── jobs/          Local job queue and worker
├── application/   Services shared by the CLI and the API
├── api/           FastAPI routes and WebSocket endpoints
└── cli/           Command-line interface
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and the decision records in
[docs/adr/](docs/adr/).

## Roadmap

| Version | Focus |
|---|---|
| v0.1 | Transcription vertical slice, CLI, project format, exports |
| v0.2 | Web UI, job progress over WebSocket, model manager UI |
| v0.3 | Speaker diarization and the speaker editor |
| v0.4 | Local translation, glossary, translation editor |
| v0.5 | Local TTS, voice mapping, previews |
| v0.6 | Multi-speaker dubbing, duration matching |
| v0.7 | Audio mixing, video muxing, multiple audio tracks |
| v0.8 | Source separation to preserve music and effects |
| v0.9 | Installer, benchmarking, performance work |
| v1.0 | Stable transcription → translation → dubbing workflow |

## Development

```bash
pip install -e ".[dev,api,asr]"

pytest -m unit              # fast, hermetic
pytest -m integration       # needs FFmpeg
pytest -m ml                # downloads and runs real models (opt-in)

ruff check . && ruff format --check .
mypy

cd frontend && npm install && npm run test && npm run build
```

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md).

## Contributing

Contributions are welcome. Please read [CONTRIBUTING.md](CONTRIBUTING.md) and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) first.

## Responsible use

Dabuj processes media that may be copyrighted, confidential, or contain
identifiable human voices. **You are responsible for having the rights or
permission to process and distribute the content you put through it.**

Voice cloning is not implemented. If it is added, it will require explicit
confirmation that you have permission to clone the voice in question. Dabuj is
a localisation tool, not an impersonation tool.

## License

Source code: [MIT](LICENSE).

AI models: **not MIT** — each carries its own license. See
[docs/MODELS.md](docs/MODELS.md) before using Dabuj commercially.
