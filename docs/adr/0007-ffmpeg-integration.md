# 0007 — FFmpeg as an external binary behind one module

**Status:** Accepted · 2026-08-10

## Context

Dabuj must read every container and codec users have, extract and resample
audio, and later mix and mux. Writing any of that is out of the question.

## Decision

Shell out to the **`ffmpeg` and `ffprobe` binaries**, through the single module
`dabuj.media.ffmpeg`. FFmpeg is **not bundled**; it is a documented
prerequisite that `dabuj doctor` checks for.

Non-negotiable rules inside that module:

- **Argument arrays only.** `shell=True` appears nowhere, so a filename
  containing `;` or `$(...)` is inert. There is an integration test that probes
  a file literally named `a; echo pwned & whoami $(id).wav`.
- **stderr is always captured** and folded into a structured error, never
  printed raw at the user.
- **Output goes to `.partial`** and is renamed on success.
- **Cancellation terminates the child process** via a token callback.
- **Progress is parsed from `-progress pipe:1`**, with stderr drained on a
  separate thread so a chatty FFmpeg cannot deadlock by filling the pipe.

## Alternatives considered

**PyAV / ffmpeg-python bindings.** In-process and avoids subprocess overhead.
Rejected: it binds Dabuj to specific libav ABI versions, makes wheels a
platform-support problem, and gives no benefit for what is fundamentally
whole-file batch conversion. Subprocess overhead is irrelevant next to a
multi-minute transcode.

**Bundling FFmpeg binaries.** Tempting for a one-click installer, and rejected
for now: FFmpeg builds vary in licensing depending on which encoders are
compiled in (GPL components, patent-encumbered codecs), and getting that wrong
in an MIT project is exactly the kind of licensing mistake this project takes
seriously. A future installer may bundle a carefully chosen LGPL build.

## Consequences

**Good**

- Every format FFmpeg supports works, with no per-codec code.
- The safety rules live in one file, so they can be reviewed and tested once.
- Upgrading FFmpeg is the user's decision and needs no Dabuj release.

**Bad**

- FFmpeg must be installed separately. `dabuj doctor` detects it and the error
  gives platform-specific install commands.
- Subprocess-per-operation costs a few tens of milliseconds. Irrelevant here.
- FFmpeg's stderr is not a stable API; parsing is limited to the machine-readable
  `-progress` stream, and stderr is only ever used as human-readable context in
  an error message.
