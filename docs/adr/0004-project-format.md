# 0004 — A project directory with a versioned JSON manifest, and no database

**Status:** Accepted · 2026-08-10

## Context

A project holds source media, intermediate artefacts, a transcript, speakers,
settings and stage state. It must survive reopening months later, resume
mid-pipeline, and be moved or backed up.

## Decision

A project is a **directory** containing a **versioned `project.json`**
manifest. There is **no database**.

```
<projects_dir>/<project-id>/
├── project.json     schema_version + everything below
├── source/          the imported media
├── cache/           per-stage artefacts
└── exports/
```

Three supporting rules:

1. **`schema_version` is written on every save** and checked on every load.
   Migrations operate on plain dicts, never on the current Pydantic models —
   using today's models to read yesterday's document would reinterpret it
   through today's defaults. A *newer* schema is refused, not guessed at.
2. **Paths are stored relative to the project directory**, POSIX-style, so a
   project folder can be moved, copied, renamed or synced and still open.
3. **Saves are atomic** — written to `project.json.partial` and renamed.

## Alternatives considered

**SQLite for everything.** Rejected. A project would stop being a portable
folder; backing one up would mean exporting from a database, and hand-inspecting
a broken project would need a SQL client. No concurrent writers exist to justify
it.

**PostgreSQL or another server database.** Rejected outright for a single-user
local tool.

**SQLite for application-level state, JSON for project contents.** A reasonable
hybrid, and the one to adopt *if* listing many projects ever becomes slow.
Deferred: listing currently means reading N small JSON files, which is fast for
realistic project counts, and adding a second source of truth before it is
needed invites the two drifting apart.

## Consequences

**Good**

- A project is a folder: copy it, zip it, sync it, diff it, inspect it.
- Uninstalling Dabuj does not strand the user's data.
- Migrations are pure functions over dicts, so they are directly testable.
- An atomic save means a crash never destroys hours of processing.

**Bad**

- Listing projects is O(N) file reads. Acceptable now; SQLite indexes it later.
- A very large transcript makes a large JSON file. Whole-file rewrites on every
  save would become noticeable for a feature-length film; if that bites, the
  transcript moves to its own file beside the manifest.
- No transactions across projects. Not needed — nothing spans projects.
