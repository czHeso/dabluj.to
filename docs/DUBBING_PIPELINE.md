# The dubbing pipeline

> **Status: mostly planned.** Only the transcription stages are implemented
> today. This document describes the intended design so that the architecture
> can be reviewed before the code exists — and so that nothing here is mistaken
> for a working feature.

| Stage | Status |
|---|---|
| Media probe → audio extract → ASR | ✅ Implemented |
| Diarization | 🗺 Planned (v0.3) |
| Translation | 🗺 Planned (v0.4) |
| TTS | 🗺 Planned (v0.5) |
| Duration matching | 🗺 Planned (v0.6) |
| Mixing and muxing | 🗺 Planned (v0.7) |
| Source separation | 🗺 Planned (v0.8) |

## The full graph

```
source
  ↓
media probe ────────────────────────┐
  ↓                                 │
audio extract ──────────────┐       │
  ↓                         │       │
ASR ──────────┐             │       │
  ↓           │             │       │
diarization   │             │       │
  ↓           ↓             │       │
  └────→ translation        │       │
              ↓             │       │
             TTS            │       │
              ↓             ↓       │
             mix ───────────┘       │
              ↓                     │
             mux ───────────────────┘
              ↓
        dubbed output
```

Dependencies are declared in `pipeline/stages.py`. The important property:
**translation does not depend on diarization**, so re-running speaker detection
never discards translations.

## Implemented: transcription

`media → probe → extract audio (16 kHz mono WAV) → VAD → language detection →
recognition → timestamped transcript`

Word-level timestamps are produced where the provider supports them. They are
not a nicety: duration matching later depends on knowing where words fall.

## Planned: diarization

Answers *who spoke when*, producing `SPEAKER_00`, `SPEAKER_01`… labels that the
user can rename. The ID never changes — only the display name — because
segments, voice assignments and generated audio all reference it.

Speaker identity matters mainly because it determines which synthetic voice
speaks each line.

## Planned: translation

Three properties distinguish this from calling a translation API in a loop:

**Context.** Sentences are not translated independently. The provider receives
preceding and following segments, the speaker, and the glossary, so that
pronouns, gendered forms, formality and terminology stay consistent. A two-hour
transcript is *not* sent wholesale — a bounded context window is used.

**A glossary.** Users define exact replacements, do-not-translate terms, and
preferred renderings. Product names and technical vocabulary otherwise drift.

**Timing awareness.** Dubbing needs the translation to *fit*. The pipeline
estimates spoken duration and may produce a shortened variant. Both are kept:
`Translation.text` (faithful) and `Translation.adapted_text` (fitted). The
`Segment` model already carries both fields.

> Meaning is never silently sacrificed for timing. When adaptation changes the
> sense materially, the segment is flagged for review rather than quietly
> rewritten.

## Planned: text to speech

Each speaker maps to a target-language voice. `Speaker.voice_id` already exists
for this, so a project carries its casting decisions with it.

Voice *cloning* is not implemented and is not required for dubbing. If added, it
will require explicit confirmation of permission. Dabuj is a localisation tool,
not an impersonation tool.

## Planned: duration matching

The hard part. A Czech translation of an English line is routinely 20–30%
longer, and the dub must still fit the picture.

Levers, cheapest and least damaging first:

1. **Translation adaptation** — say it more concisely. Best quality; changes
   wording.
2. **TTS speaking rate** — where the engine supports it natively.
3. **Silence adjustment** — borrow from pauses around the segment.
4. **Time-stretching** — last resort, and strictly bounded.

Proposed thresholds, to be configurable and revisited with real material:

| Mismatch | Action |
|---|---|
| < 10% | Correct automatically |
| 10–25% | Correct and flag in the quality report |
| > 25% | Require review |

Every generated segment records its target and actual duration, so the editor
can show exactly where timing is under strain.

## Planned: mixing and muxing

**Mixing** places dubbed segments on a timeline against the original audio bed.
Ideally the original music and effects are preserved — which needs source
separation to split dialogue from background. Separation is optional: without
it, basic dubbing still works, it simply replaces the whole audio track.

**Muxing** builds the output file. The rule that matters:

> If only the audio changes, **do not re-encode the video.** Stream-copy it.

Re-encoding a two-hour film to change its audio wastes an hour and degrades
quality for no reason. The intended output keeps the original video, the
original audio as a secondary track, the dubbed track, and both subtitle tracks
— subject to what the container actually supports. Dabuj will not promise
universal stream preservation across every container and codec combination.

## Quality report

After processing, a report links problems to the segments that caused them:
low recognition confidence, uncertain speaker assignment, terminology warnings,
segments exceeding their target duration.

This is what makes a two-hour dub reviewable: it directs attention at the dozen
segments that need a human instead of asking for a full listen-through.
