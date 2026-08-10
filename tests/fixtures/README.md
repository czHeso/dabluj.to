# Test fixtures

**This directory is intentionally almost empty.**

Dabuj does not commit media fixtures. Instead:

- **Integration tests generate their own** with FFmpeg — a sine tone, or a
  test-pattern video with an audio track (see `tone_wav` and `tone_video` in
  `tests/conftest.py`). Generating them means no licensing questions, no
  repository bloat, and a fixture that is always exactly what the test expects.

- **ML tests download a speech sample on demand** (`tests/ml/`). Real speech is
  needed to verify recognition, and it is fetched at run time rather than
  committed. The clip used is an excerpt of John F. Kennedy's 1961 inaugural
  address — a work of the US federal government, and therefore public domain —
  as distributed in the MIT-licensed faster-whisper repository.

If you add a fixture that genuinely must be committed, keep it tiny, state its
licence here, and make sure it is one we have the right to redistribute.
