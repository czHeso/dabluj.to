/**
 * The transcript editor.
 *
 * Editing is non-destructive: the ASR output stays in `raw_text`, and an
 * edited segment is badged so the user can see what a human has touched.
 */

import { useEffect, useMemo, useState } from 'react'
import { api } from '../api/client'
import type { ProjectDocument, Segment, Speaker, Transcript } from '../api/types'
import { Empty, ErrorBox, Loading, formatPreciseTime } from '../components/common'

/** Below this ASR confidence a segment is flagged for review. */
const LOW_CONFIDENCE = 0.5

interface Props {
  projectId: string
  onBack: () => void
}

export function TranscriptPage({ projectId, onBack }: Props): JSX.Element {
  const [project, setProject] = useState<ProjectDocument | null>(null)
  const [transcript, setTranscript] = useState<Transcript | null>(null)
  const [speakers, setSpeakers] = useState<Record<string, Speaker>>({})
  const [error, setError] = useState<unknown>(null)
  const [exported, setExported] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setError(null)

    void Promise.all([api.getProject(projectId), api.getTranscript(projectId)])
      .then(([document, payload]) => {
        if (cancelled) return
        setProject(document)
        setTranscript(payload.transcript)
        setSpeakers(payload.speakers)
      })
      .catch((caught) => !cancelled && setError(caught))

    return () => {
      cancelled = true
    }
  }, [projectId])

  const lowConfidenceCount = useMemo(
    () =>
      (transcript?.segments ?? []).filter(
        (segment) =>
          segment.confidence !== null &&
          segment.confidence < LOW_CONFIDENCE &&
          segment.edited_text === null,
      ).length,
    [transcript],
  )

  const handleEdit = async (segmentId: string, text: string): Promise<void> => {
    try {
      const updated = await api.updateSegment(projectId, segmentId, { text })
      setTranscript((current) =>
        current
          ? {
              ...current,
              segments: current.segments.map((segment) =>
                segment.id === segmentId ? updated : segment,
              ),
            }
          : current,
      )
    } catch (caught) {
      setError(caught)
    }
  }

  const handleSplit = async (segment: Segment): Promise<void> => {
    const midpoint = (segment.start + segment.end) / 2
    try {
      setTranscript(await api.splitSegment(projectId, segment.id, midpoint))
    } catch (caught) {
      setError(caught)
    }
  }

  const handleMergeWithNext = async (index: number): Promise<void> => {
    const segments = transcript?.segments ?? []
    const current = segments[index]
    const next = segments[index + 1]
    if (!current || !next) return
    try {
      setTranscript(await api.mergeSegments(projectId, current.id, next.id))
    } catch (caught) {
      setError(caught)
    }
  }

  const handleExport = async (format: string): Promise<void> => {
    setError(null)
    try {
      const result = await api.exportTranscript(projectId, format)
      setExported(result.path)
    } catch (caught) {
      setError(caught)
    }
  }

  if (error && !transcript) return <ErrorBox error={error} />
  if (!project || !transcript) return <Loading what="Loading transcript" />

  return (
    <>
      <div className="page-header">
        <button onClick={onBack} style={{ marginBottom: '0.75rem' }}>
          ← Projects
        </button>
        <h1>{project.name}</h1>
        <p>
          {transcript.segments.length} segments
          {transcript.language ? ` · ${transcript.language}` : null}
          {transcript.language_confidence
            ? ` (${Math.round(transcript.language_confidence * 100)}% confident)`
            : null}
        </p>
      </div>

      <ErrorBox error={error} />

      {project.warnings.length > 0 ? (
        <div className="warning">
          {project.warnings.map((warning) => (
            <p key={warning} className="small" style={{ margin: 0 }}>
              {warning}
            </p>
          ))}
        </div>
      ) : null}

      {lowConfidenceCount > 0 ? (
        <div className="warning small">
          {lowConfidenceCount} segment{lowConfidenceCount === 1 ? '' : 's'} had low
          recognition confidence and may need review. They are marked below.
        </div>
      ) : null}

      <div className="card">
        <div className="row">
          <strong className="small">Export</strong>
          {['srt', 'vtt', 'json', 'txt'].map((format) => (
            <button key={format} onClick={() => void handleExport(format)}>
              {format.toUpperCase()}
            </button>
          ))}
        </div>
        {exported ? (
          <p className="small muted" style={{ margin: '0.6rem 0 0' }}>
            Written to <span className="mono">{exported}</span>
          </p>
        ) : null}
      </div>

      {transcript.segments.length === 0 ? (
        <Empty
          title="No speech was found"
          hint="The audio may be silent, or contain only music."
        />
      ) : (
        <div className="card card--flush">
          {transcript.segments.map((segment, index) => (
            <SegmentRow
              key={segment.id}
              segment={segment}
              speaker={segment.speaker_id ? speakers[segment.speaker_id] : undefined}
              canMerge={index < transcript.segments.length - 1}
              onEdit={(text) => void handleEdit(segment.id, text)}
              onSplit={() => void handleSplit(segment)}
              onMerge={() => void handleMergeWithNext(index)}
            />
          ))}
        </div>
      )}
    </>
  )
}

function SegmentRow({
  segment,
  speaker,
  canMerge,
  onEdit,
  onSplit,
  onMerge,
}: {
  segment: Segment
  speaker: Speaker | undefined
  canMerge: boolean
  onEdit: (text: string) => void
  onSplit: () => void
  onMerge: () => void
}): JSX.Element {
  const currentText = segment.edited_text ?? segment.raw_text
  const [draft, setDraft] = useState(currentText)

  // Keep the textarea in step when the segment changes underneath it (a split
  // or merge replaces the whole list).
  useEffect(() => setDraft(currentText), [currentText])

  const isEdited = segment.edited_text !== null && segment.edited_text !== segment.raw_text
  const isLowConfidence =
    segment.confidence !== null && segment.confidence < LOW_CONFIDENCE && !isEdited

  return (
    <div className={`segment${isLowConfidence ? ' segment--low-confidence' : ''}`}>
      <div className="segment-meta">
        <div>{formatPreciseTime(segment.start)}</div>
        <div>{formatPreciseTime(segment.end)}</div>
        <div style={{ marginTop: '0.35rem' }}>
          {speaker?.display_name ?? segment.speaker_id ?? ''}
        </div>
        {segment.confidence !== null ? (
          <div style={{ marginTop: '0.35rem' }}>
            {Math.round(segment.confidence * 100)}%
          </div>
        ) : null}
      </div>

      <div>
        <textarea
          aria-label={`Transcript text starting at ${formatPreciseTime(segment.start)}`}
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={() => draft !== currentText && onEdit(draft)}
        />
        <div className="segment-actions">
          {isEdited ? <span className="badge badge--edited">edited</span> : null}
          {isEdited ? (
            <button title="Restore the original recognition output" onClick={() => onEdit(segment.raw_text)}>
              Revert
            </button>
          ) : null}
          <button onClick={onSplit}>Split</button>
          {canMerge ? <button onClick={onMerge}>Merge next</button> : null}
        </div>
        {isEdited ? (
          <p className="small muted" style={{ margin: '0.35rem 0 0' }}>
            Original: {segment.raw_text}
          </p>
        ) : null}
      </div>
    </div>
  )
}
