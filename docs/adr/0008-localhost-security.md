# 0008 — Origin and Host checking, because localhost is not private

**Status:** Accepted · 2026-08-10

## Context

Dabuj binds to `127.0.0.1`. That stops other machines on the network. It does
**not** stop the browser the user is already running: any page they visit can
issue requests to `http://127.0.0.1:7860`.

Two concrete attacks follow.

**Cross-site requests.** `evil.example` runs `fetch('http://127.0.0.1:7860/api/projects/x', {method:'DELETE'})`.
CORS is often assumed to prevent this. It does not. CORS governs whether the
attacker may *read the response*; the request is still sent and still executes.
When the side effect is the damage — deleting a project, starting a job — CORS
alone is no defence.

**DNS rebinding.** The attacker points `evil.example` at `127.0.0.1`. The browser
now believes the local server *is* `evil.example`, treats it as same-origin, and
sends no `Origin` header at all. Origin checking alone does not catch this.

## Decision

A middleware that runs **before routing** and enforces both:

1. **`Host` must be a loopback name.** A rebound request arrives with
   `Host: evil.example` and is rejected with **421 Misdirected Request**.
2. **`Origin`, if present, must be one of Dabuj's own.** Anything else is
   rejected with **403**.

CORS is configured with an explicit origin allow-list and
`allow_credentials=False`. **`Access-Control-Allow-Origin: *` appears nowhere.**

The allow-list is built from the port the server *actually* listens on, not the
configured preference — a distinction that caused a real bug during development,
where `--port` or the auto-port fallback blocked the application's own UI. There
is a regression test for it.

## Alternatives considered

**A session token in the URL.** Stronger, and used by Jupyter. Deferred: it
complicates the "open your browser and go" experience, and Origin + Host checking
already closes both known attacks for a loopback-only service. It becomes
necessary if `allow_lan` is ever more than a power-user escape hatch.

**Nothing beyond binding to loopback.** Rejected. It is the common mistake, and
it leaves both attacks above wide open.

**Authentication (password / OS keyring).** Disproportionate for a single-user
local tool with no multi-user concept, and it would need secure credential
storage on three platforms.

## Consequences

**Good**

- A malicious page cannot drive the API or read transcripts. Both are asserted
  by tests against the live app, not merely by inspection.
- No credentials to manage, store or leak.
- `allow_lan` remains available for users who genuinely want it, and the server
  prints a prominent warning when it is on.

**Bad**

- Anything embedding the UI on another origin must be added to the allow-list.
  The Vite dev server (`:5173`) already is.
- `allow_lan = true` exposes an **unauthenticated** service to the network. This
  is documented loudly in [SECURITY.md](../SECURITY.md) and warned about at
  start-up, but it remains a real footgun for anyone who enables it.
