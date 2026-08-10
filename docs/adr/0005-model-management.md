# 0005 — Models are never bundled, always verified, and always consented to

**Status:** Accepted · 2026-08-10

## Context

Dabuj needs multi-gigabyte AI models. They carry licenses that differ from the
project's MIT, they are far too large for git, and downloading arbitrary files
from the internet and unpacking them is a well-known way to build a remote code
execution vector.

## Decision

1. **No model weights are committed or bundled.** Not in git, not in the wheel,
   not in any future installer.
2. **Nothing is downloaded without explicit consent.** `plan_install()` resolves
   the exact file list, total size, source and license *before* any transfer;
   that plan is shown and confirmed. There is no silent download path anywhere.
3. **Files land in `.partial` and are renamed only after verification.** An
   interrupted download can therefore never masquerade as a complete model.
4. **Checksums are verified where published.** Hugging Face exposes the SHA-256
   of every LFS file, which covers the weights — the files that matter. Files
   without a published checksum are verified by size and reported as such.
5. **Every remote path is confined** to the model directory before it is written,
   so a repository listing `../../.ssh/id_rsa` is rejected.
6. **No archives are extracted.** Only plain files are fetched, which removes
   the zip-slip surface entirely.
7. **Install state is a marker file written last.** A directory of
   half-downloaded weights has no marker and is correctly reported as *not
   installed*.

## Alternatives considered

**`huggingface_hub.snapshot_download`.** Convenient, and already an indirect
dependency. Rejected as the primary path because it makes progress reporting,
cancellation and pre-download size disclosure awkward, and it caches into its
own global directory rather than the user-configurable models directory. Plain
HTTPS with `httpx` gives full control over all four.

**Bundling a small default model in the installer.** Rejected. Even the smallest
Whisper model is 75 MB, it would bind the installer to one licensing story, and
"no weights in the repository" is a much easier rule to keep honest than "no
weights except this one".

## Consequences

**Good**

- `git clone` stays small; CI never downloads gigabytes.
- The user always knows what is being fetched, from where, and under what terms.
- Downloads resume via HTTP Range rather than restarting at three gigabytes.
- A corrupt or tampered download fails verification instead of being loaded.

**Bad**

- First run requires network access and an explicit download step. This is a
  deliberate trade: transparency over convenience.
- The catalog hard-codes repository IDs, so an upstream rename breaks an entry
  until the catalog is updated. The error message says exactly that.
- Gated models (pyannote) will need a user-supplied access token, handled as an
  explicit opt-in step when diarization lands.
