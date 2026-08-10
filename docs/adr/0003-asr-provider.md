# 0003 — faster-whisper as the default speech recognition backend

**Status:** Accepted · 2026-08-10

## Context

The first milestone is transcription. The backend had to satisfy every one of
these, not most of them:

- genuinely usable **CPU-only** — many target users have no discrete GPU;
- strong **English, German and Czech**;
- **word-level timestamps** (dubbing needs them later);
- **language detection** with a confidence value;
- **permissive licensing** for both runtime *and* weights;
- **mature Windows support**;
- multiple model sizes to back the quality profiles.

## Decision

Use **faster-whisper** (CTranslate2) as the default `ASRProvider`, with
Whisper-family models from the SYSTRAN CTranslate2 conversions.

## Alternatives considered

| Option | Why not |
|---|---|
| **openai-whisper** (reference PyTorch) | Correct and MIT, but slow, memory-hungry, and pulls the full PyTorch stack — a multi-gigabyte install for a CPU-only laptop user. |
| **whisper.cpp** | Excellent CPU performance and a tiny dependency footprint. Rejected as the *default* because it needs a compiled binary or platform wheels, complicating installation. It remains the strongest candidate for a second provider, which is precisely what the provider abstraction exists for. |
| **NVIDIA Parakeet / Canary** | Very strong accuracy, but English-centric or with limited Czech support, and tied to the NeMo stack. Fails the language and CPU requirements. |
| **Cloud ASR APIs** | Fails the core premise of the product. |

## Verification (2026-08-10)

- `faster-whisper` — MIT, actively maintained, Python ≥ 3.9.
  <https://github.com/SYSTRAN/faster-whisper>
- Whisper weights — MIT.
  <https://github.com/openai/whisper/blob/main/LICENSE>
- CTranslate2 publishes **no wheels and no sdist for Python 3.13**, which is why
  Dabuj caps its supported Python at 3.12. See [ADR 0009](0009-python-version.md).
- GPU inference requires **CUDA 12 and cuDNN 9**.

Measured on the development machine (RTX 3070, Ryzen-class CPU) transcribing an
11-second clip with `whisper-tiny`, `--device cpu --precision int8`: **7.9×
realtime**. This is a single spot check, not a benchmark; `dabuj benchmark`
(v0.9) will measure properly on the user's own hardware.

## Consequences

**Good**

- int8 quantisation makes CPU-only genuinely practical.
- Word timestamps, VAD and language detection come built in.
- MIT throughout, so commercial use is unencumbered.
- Five model sizes map cleanly onto the four quality profiles.

**Bad**

- Ties the default path to CTranslate2's Python support, capping us at 3.12.
- GPU users need matching CUDA 12 / cuDNN 9 libraries; the provider detects this
  and reports it clearly rather than failing obscurely.
- Whisper can hallucinate on silence and music. Mitigated by enabling VAD by
  default and by surfacing per-segment confidence in the editor.
