# 0001 — A local web application, not a desktop GUI or a cloud service

**Status:** Accepted · 2026-08-10

## Context

Dabuj must let a user process private media on their own machine, with a
capable UI: a transcript editor, live progress for multi-hour jobs, a model
manager. Three shapes were possible:

1. **Cloud SaaS** — contradicts the entire premise. Rejected outright.
2. **Native desktop GUI** (Qt/PySide, wxWidgets) — a heavy dependency, a
   licensing question in Qt's case, slow text-editing UI work, and packaging
   pain on three platforms.
3. **Local web application** — a Python backend serving a browser UI on
   loopback.

## Decision

Build a **local web application**. The backend binds to `127.0.0.1`, serves the
built React frontend, and opens the user's browser.

## Alternatives considered

**Qt/PySide desktop app.** Best-in-class native feel and real file dialogs. But
building a good transcript editor in Qt is far more work than in the DOM, the
dependency is large, and PySide/PyQt licensing needed care for an MIT project.

**Electron/Tauri from the start.** Would give native file pickers immediately.
Rejected for the MVP because it adds a whole toolchain (Node packaging, Rust for
Tauri) before the core pipeline exists. The frontend/backend split means it can
be added later without rewriting the frontend.

## Consequences

**Good**

- The UI is HTML/CSS/React: fast to build, accessible by default, familiar.
- No GUI toolkit dependency; the backend is importable and testable headlessly.
- The same API serves the browser, automation and a future remote worker.
- A Tauri wrapper later needs no frontend rewrite.

**Bad, and what we do about it**

- *The browser cannot give us a real filesystem path for a dropped file.* Dabuj
  therefore offers an explicit local-path field for large media, so a 12 GB file
  is read in place rather than uploaded through localhost. Drag-and-drop upload
  remains available for smaller files.
- *A local HTTP server is reachable by any web page the user visits.* This is a
  real attack surface that a desktop app would not have. It is addressed by
  origin and Host checking — see [ADR 0008](0008-localhost-security.md).
- *Users must have a browser.* Acceptable; every target platform has one.
- *No native file dialogs.* Accepted for now; a Tauri wrapper would fix it.
