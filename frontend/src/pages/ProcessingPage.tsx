/** Live processing view for a running job. */

import { api } from '../api/client'
import type { Job } from '../api/types'
import { ErrorBox, Loading, ProgressBar, formatTime } from '../components/common'
import { useJobProgress } from '../hooks/useJobProgress'

const STAGE_MARKS: Record<string, string> = {
  completed: '✓',
  running: '●',
  pending: '○',
  failed: '✗',
  cancelled: '⊘',
  skipped: '⤼',
}

interface Props {
  jobId: string
  onOpenProject: (projectId: string) => void
}

export function ProcessingPage({ jobId, onOpenProject }: Props): JSX.Element {
  const { job, latest } = useJobProgress(jobId)

  if (!job) return <Loading what="Connecting to the job" />

  const finished = job.status === 'completed'
  const overall = latest?.overall_progress ?? job.overall_progress ?? 0

  return (
    <>
      <div className="page-header">
        <h1>{job.title}</h1>
        <p>
          {job.status === 'running'
            ? 'Processing on this computer.'
            : `Job ${job.status}.`}
        </p>
      </div>

      {job.error ? <ErrorBox error={new ApiErrorLike(job.error)} /> : null}

      <div className="card">
        <ProgressBar value={overall} />
        <div className="row" style={{ marginTop: '0.6rem', justifyContent: 'space-between' }}>
          <strong>{Math.round(overall * 100)}%</strong>
          <span className="small muted">
            {latest?.elapsed_seconds ? `Elapsed ${formatTime(latest.elapsed_seconds)}` : null}
            {/* Only shown once the estimate is meaningful; the backend
                withholds it until then rather than inventing a number. */}
            {latest?.eta_seconds ? ` · About ${formatTime(latest.eta_seconds)} left` : null}
          </span>
        </div>

        <ul className="stages">
          {job.stages.map((stage) => {
            const state = latest?.stage === stage.stage ? 'running' : stage.state
            return (
              <li key={stage.stage} className={`stage--${state}`}>
                <span className="stage-mark" aria-hidden="true">
                  {STAGE_MARKS[state] ?? '○'}
                </span>
                <span>{stage.label}</span>
                {state === 'running' && latest?.message ? (
                  <span className="small muted">— {latest.message}</span>
                ) : null}
              </li>
            )
          })}
        </ul>

        {latest && (latest.device || latest.model_id || latest.realtime_factor) ? (
          <p className="small muted" style={{ marginTop: '0.75rem', marginBottom: 0 }}>
            {latest.model_id ? `Model ${latest.model_id}` : null}
            {latest.device ? ` · ${latest.device.toUpperCase()}` : null}
            {latest.realtime_factor
              ? ` · ${latest.realtime_factor.toFixed(1)}× realtime`
              : null}
          </p>
        ) : null}
      </div>

      <div className="row">
        {job.status === 'running' || job.status === 'queued' ? (
          <button onClick={() => void api.cancelJob(job.id)}>Cancel</button>
        ) : null}
        {finished && job.project_id ? (
          <button className="primary" onClick={() => onOpenProject(job.project_id as string)}>
            Open transcript
          </button>
        ) : null}
      </div>
    </>
  )
}

/** Adapts a job's stored error payload to what ErrorBox renders. */
class ApiErrorLike extends Error {
  payload: NonNullable<Job['error']>
  constructor(payload: NonNullable<Job['error']>) {
    super(payload.summary)
    this.payload = payload
  }
  get suggestions(): string[] {
    return this.payload.suggestions ?? []
  }
}
