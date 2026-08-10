/** Small presentational components shared across pages. */

import type { ReactNode } from 'react'
import type { ApiErrorPayload } from '../api/types'

/** Anything carrying the backend's structured error payload. */
function payloadOf(error: unknown): ApiErrorPayload | null {
  if (typeof error === 'object' && error !== null && 'payload' in error) {
    const candidate = (error as { payload: unknown }).payload
    if (typeof candidate === 'object' && candidate !== null && 'summary' in candidate) {
      return candidate as ApiErrorPayload
    }
  }
  return null
}

/**
 * Renders a backend error the way the CLI does: what failed, why, and what to
 * try. Never a bare status code.
 *
 * Duck-typed on the payload rather than on a class, so a job's stored error --
 * which arrives as plain JSON, not as a thrown ApiError -- renders identically.
 */
export function ErrorBox({ error }: { error: unknown }): JSX.Element | null {
  if (!error) return null

  const payload = payloadOf(error)
  const summary = payload ? payload.summary : String(error)
  const reason = payload?.reason ?? null
  const suggestions = payload?.suggestions ?? []

  return (
    <div className="error" role="alert">
      <h3>{summary}</h3>
      {reason ? <p className="small muted">{reason}</p> : null}
      {suggestions.length > 0 ? (
        <>
          <p className="small" style={{ margin: 0 }}>
            Try:
          </p>
          <ul className="small">
            {suggestions.map((suggestion) => (
              <li key={suggestion}>{suggestion}</li>
            ))}
          </ul>
        </>
      ) : null}
    </div>
  )
}

export function Empty({
  title,
  hint,
  action,
}: {
  title: string
  hint?: string
  action?: ReactNode
}): JSX.Element {
  return (
    <div className="empty">
      <p style={{ fontWeight: 550, color: 'var(--text)' }}>{title}</p>
      {hint ? <p className="small">{hint}</p> : null}
      {action}
    </div>
  )
}

export function Loading({ what = 'Loading' }: { what?: string }): JSX.Element {
  return (
    <p className="muted small" role="status">
      {what}…
    </p>
  )
}

export function ProgressBar({ value }: { value: number | null }): JSX.Element {
  const percent = Math.round(Math.min(1, Math.max(0, value ?? 0)) * 100)
  return (
    <div
      className="bar"
      role="progressbar"
      aria-valuenow={percent}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span style={{ width: `${percent}%` }} />
    </div>
  )
}

/** Format seconds as `M:SS` or `H:MM:SS`. */
export function formatTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—'
  const total = Math.max(0, Math.round(seconds))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, '0')}:${String(secs).padStart(2, '0')}`
  }
  return `${minutes}:${String(secs).padStart(2, '0')}`
}

/** Format a timestamp with centiseconds, for the transcript editor. */
export function formatPreciseTime(seconds: number): string {
  const total = Math.max(0, seconds)
  const minutes = Math.floor(total / 60)
  const secs = Math.floor(total % 60)
  const centis = Math.floor((total % 1) * 100)
  return `${minutes}:${String(secs).padStart(2, '0')}.${String(centis).padStart(2, '0')}`
}

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return `${value.toFixed(unit <= 1 ? 0 : 1)} ${units[unit]}`
}
