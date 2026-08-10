# Security model

This document describes what Dabuj defends against, how, and what it does not.
For reporting a vulnerability, see [SECURITY.md](../SECURITY.md) in the
repository root.

## Threat model

Dabuj is a **single-user local application**. There are no accounts, no
multi-tenancy and no trust boundary between users. The realistic threats are:

| # | Threat | Status |
|---|---|---|
| 1 | A malicious web page calls the local API from the user's browser | Mitigated |
| 2 | DNS rebinding defeats the loopback binding | Mitigated |
| 3 | Another machine on the network reaches the service | Mitigated by default |
| 4 | A malicious filename injects a shell command via FFmpeg | Mitigated |
| 5 | A path from user input or a remote index escapes its directory | Mitigated |
| 6 | A tampered or corrupt model download | Mitigated |
| 7 | A crafted media file exploits FFmpeg | Partially — see below |
| 8 | A malicious model executes code when loaded | Partially — see below |
| 9 | Local malware already running as the user | Out of scope |

## 1 & 2 — The browser is the attack surface

Binding to `127.0.0.1` stops other machines. It does **not** stop the user's own
browser: any page they visit can send requests to `http://127.0.0.1:7860`.

**CORS is not sufficient.** It governs whether an attacker may *read* the
response. The request is still sent and still executes. For a `DELETE` that
destroys a project, being unable to read the reply is no consolation.

Dabuj therefore rejects, **before routing**:

- any request whose `Host` is not a loopback name → **421** (defeats DNS
  rebinding, where the browser sends no `Origin` at all);
- any request whose `Origin` is present and not Dabuj's own → **403**.

CORS is additionally configured with an explicit allow-list and
`allow_credentials=False`. `Access-Control-Allow-Origin: *` appears nowhere.

See [ADR 0008](adr/0008-localhost-security.md). Both defences are covered by
tests that run against the real application.

## 3 — Network exposure

Default binding is `127.0.0.1`. Setting `server.allow_lan = true` binds
`0.0.0.0` instead.

> **Warning.** `allow_lan` exposes an **unauthenticated** service to your
> network. Anyone who can reach the port can read your transcripts, start jobs
> and delete projects. Only enable it on a network you control, and prefer an
> SSH tunnel. Dabuj prints a warning at start-up when it is on.

## 4 — Command injection

Every FFmpeg invocation is built as an **argument array**. `shell=True` is used
nowhere in the codebase, so shell metacharacters in a filename are inert.

There is an integration test that probes a file literally named
`a; echo pwned & whoami $(id).wav` and asserts it is treated as a filename.

## 5 — Path traversal

`resolve_within(base, candidate)` is the single choke point for turning
untrusted input into a filesystem path. It fully resolves both paths — so
symlinks are followed before comparison — and raises `UnsafePathError` if the
result would escape.

It guards project IDs, cache paths, export paths and every file path returned by
a remote model index. A repository claiming a file called `../../.ssh/id_rsa`
is rejected, not written.

The one deliberate exception is the "open a local file by path" endpoint, whose
entire purpose is to read the user's own media from anywhere on their disk. It
validates that the path is absolute and a regular file. What protects it is
threat 1's origin guard: a web page cannot reach it in the first place.

## 6 — Model downloads

See [ADR 0005](adr/0005-model-management.md). In summary: HTTPS with
certificate verification always on; SHA-256 verified against the publisher's
checksum where published; `.partial` files renamed only after verification;
every remote path confined; **no archives extracted**, which removes the
zip-slip surface entirely.

Certificate verification is never disabled. There is no setting to disable it.
On machines behind a TLS-inspecting proxy, Dabuj verifies against the
**operating system trust store** — where such a proxy's root certificate is
actually installed — rather than only certifi's bundle.

## 7 — Crafted media (partial)

Dabuj passes media to FFmpeg, a large C codebase with a real history of
memory-safety bugs. A malicious file could in principle exploit it.

Mitigations: FFmpeg runs as a **separate process**, so a crash does not take the
application down, and a timeout bounds it. Dabuj does not sandbox it further.

Keep FFmpeg updated, and treat media from untrusted sources with the same
caution you would in any media player.

## 8 — Malicious models (partial)

Dabuj downloads only CTranslate2 model files (`.bin`, `.json`, `.txt`), which
are data consumed by the CTranslate2 runtime, not Python pickles. There is no
`torch.load` of an untrusted checkpoint anywhere in the codebase, so the classic
pickle-deserialisation RCE does not apply to the current catalog.

Models are fetched only from the pinned repositories in the built-in catalog.
Dabuj does not support arbitrary user-supplied model URLs; if that is ever added,
it will need its own review.

## 9 — Out of scope

Malware already running as the user can read the same files Dabuj can. No
application-level control helps. Dabuj does not encrypt project data at rest;
use full-disk encryption if that matters to you.

## Reporting

See [SECURITY.md](../SECURITY.md). Please do not open a public issue for a
vulnerability.
