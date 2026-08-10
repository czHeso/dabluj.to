# 0002 — React + TypeScript + Vite, and no state-management library

**Status:** Accepted · 2026-08-10

## Context

The UI needs a transcript editor with hundreds of editable rows, live job
progress, and a model manager. It runs only in a modern local browser — there
is no legacy browser matrix to support.

## Decision

**React 18 + TypeScript + Vite.** No router, no state-management library, no
data-fetching library, no CSS framework.

## Alternatives considered

**Svelte / SolidJS.** Smaller and faster. React was chosen for contributor
familiarity: this is an open-source project that wants outside contributions,
and React is the stack most web developers already know.

**Plain JavaScript.** Rejected. The frontend consumes a structured API with
transcripts, segments, jobs and progress events; TypeScript catches contract
drift at build time, which is exactly where it is cheapest.

**Redux / Zustand / TanStack Query.** Rejected for now. There are five views and
one live data source. `useState` plus a single `api` module covers it. Adding a
state library before there is state worth managing is the kind of speculative
abstraction the project explicitly avoids.

**React Router.** Rejected. A local single-user tool has no URLs worth
deep-linking. Navigation is one discriminated-union state field in `App.tsx`,
which is trivially swappable if that changes.

**Tailwind / a component library.** Rejected. The application needs perhaps 300
lines of CSS. A framework would add a build step and a large class vocabulary
for no benefit, and hand-written CSS custom properties give clean light/dark
theming.

## Consequences

**Good**

- Fast builds and instant HMR.
- One `api/client.ts` is the entire backend contract; no component calls `fetch`.
- The production bundle is ~53 kB gzipped, served from disk with no CDN.
- No external network requests at all: a strict requirement for a privacy tool.

**Bad**

- Navigation state is hand-rolled; a router will be needed if deep links appear.
- Hand-written types can drift from the backend. Mitigated by keeping them in
  one file and by the API integration tests. Generating them from the OpenAPI
  schema is a reasonable future step.
