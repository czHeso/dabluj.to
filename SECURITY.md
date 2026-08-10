# Security policy

## Supported versions

Dabuj is pre-1.0. Only the latest release receives security fixes.

| Version | Supported |
|---|---|
| 0.1.x | ✅ |
| < 0.1 | ❌ |

## Reporting a vulnerability

**Please do not open a public issue.**

Use GitHub's [private vulnerability reporting](https://github.com/czHeso/dabluj.to/security/advisories/new)
for this repository.

Please include:

- what the vulnerability allows an attacker to do;
- the steps to reproduce it;
- the affected version and platform;
- a proof of concept, if you have one.

**What to expect:** an acknowledgement within a few days, an assessment and
planned fix within two weeks, and credit in the advisory unless you prefer
otherwise. Dabuj is a volunteer project, so please allow reasonable time before
public disclosure.

## Scope

Dabuj is a **single-user local application**. Its threat model, and what is
mitigated versus explicitly out of scope, is documented in detail in
[docs/SECURITY.md](docs/SECURITY.md).

### In scope

- Bypassing the origin or Host checks that protect the local API from web pages.
- Path traversal escaping the project, cache, models or export directories.
- Command injection through filenames or any other user-controlled input.
- Model-download flaws: checksum bypass, TLS verification bypass, writing
  outside the models directory.
- Any path that transmits user media or transcript content off the machine.

### Out of scope

- **`server.allow_lan = true`.** This deliberately exposes an unauthenticated
  service to your network. That is documented, warned about at start-up, and is
  the intended behaviour of the setting — not a vulnerability.
- **Vulnerabilities in FFmpeg itself.** Report those to
  [FFmpeg](https://ffmpeg.org/security.html). Dabuj runs it as a separate
  process with a timeout, and keeping it updated is the user's responsibility.
- **Vulnerabilities in AI model runtimes.** Report upstream.
- **Local malware already running as the user.** It can read the same files
  Dabuj can; no application-level control changes that.
- Anything requiring physical access to an unlocked machine.

## A note on the model catalog

Dabuj downloads only CTranslate2 model files — data consumed by the runtime, not
Python pickles. There is no `torch.load` of an untrusted checkpoint anywhere in
the codebase.

If you find a way to make Dabuj fetch or load a model from a source outside its
pinned catalog, that **is** in scope and we would very much like to hear about
it.
