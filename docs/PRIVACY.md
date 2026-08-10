# Privacy

Privacy is not a feature of Dabuj that can be switched off. It is the reason the
project exists.

## The short version

- **Dabuj sends no telemetry, and contains no code to send any.**
- With local providers — the only kind currently implemented — **your media
  never leaves your computer**.
- The server binds to `127.0.0.1` and is not reachable from your network.
- Transcript text, media contents and voice samples are **never written to
  logs**.

## What leaves your computer

Exactly one thing, and only when you ask for it:

**Model downloads.** When you install a model, Dabuj makes an HTTPS request to
the model's source (currently `huggingface.co`) to list its files, then
downloads them. That request necessarily reveals to that host that some computer
at your IP address downloaded that model. It contains nothing about you, your
media or your projects.

Nothing else. No update checks, no crash reporting, no usage statistics, no
license validation, no analytics.

## What stays on your computer

Everything else:

| Data | Where it lives |
|---|---|
| Source media | Your project directory |
| Extracted audio | The project cache |
| Transcripts and translations | `project.json` |
| Speaker labels and names | `project.json` |
| Model weights | Your models directory |
| Logs | Your logs directory |
| Settings | `dabuj.toml` |

## Logging

Dabuj uses structured logs, written to `<data-dir>/logs/dabuj.jsonl`.

**Never logged:**

- transcript text, translations, or any recognised speech;
- media file contents;
- voice samples.

**Logged:** timestamps, log level, module, and identifiers — project ID, job ID,
stage, provider, model ID. Progress events are deliberately built to carry no
transcript text, so that content cannot leak into a log or a browser console.

Filenames appear only at `DEBUG` level, which is off unless you pass `--debug`.

Logs stay on your machine. Nothing uploads them. `dabuj doctor` produces a
diagnostic report you can share, and it is designed to exclude content.

## Hardware detection

Dabuj inspects your machine to recommend a quality profile: OS, CPU model, core
count, RAM, GPU model and VRAM, available acceleration backends, free disk space.

It deliberately does **not** collect anything that identifies the machine or
you — no serial numbers, MAC addresses, hostnames, user names or machine GUIDs.
There is a test asserting the report contains none of these.

This information never leaves the computer; it is used locally and shown to you
in `dabuj system-info`.

## Cloud providers

None are implemented. The architecture allows them later, behind these rules:

1. A master switch, `privacy.allow_cloud_providers`, **off by default**. While
   off, a cloud provider cannot be selected at all.
2. **No silent fallback.** If a local model fails, Dabuj reports the failure. It
   never quietly sends your media somewhere else instead.
3. The UI states the current mode at all times. Today it reads *"Local
   processing — your media stays here"*. Enabling a cloud provider changes that
   badge, visibly and permanently, while it is on.

## Telemetry

Off. There is no code to turn on.

If telemetry is ever added it will be opt-in, documented in detail here before
release, and will never include media, transcript content, filenames, voice
samples or personal data.

## Your data is yours

Projects are plain folders with a JSON manifest. You can copy, back up, inspect
or delete them without Dabuj. Uninstalling the application does not take your
data with it, and nothing is stored in a format only Dabuj can read.
