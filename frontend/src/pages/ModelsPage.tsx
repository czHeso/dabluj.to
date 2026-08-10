/**
 * The model manager.
 *
 * The rule this page exists to enforce: a user is always shown the exact
 * download size, source and license *before* anything is fetched.
 */

import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { InstallPlan, ModelInfo } from '../api/types'
import { ErrorBox, Loading, ProgressBar, formatBytes } from '../components/common'
import { useJobProgress } from '../hooks/useJobProgress'

export function ModelsPage(): JSX.Element {
  const [models, setModels] = useState<ModelInfo[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [plan, setPlan] = useState<InstallPlan | null>(null)
  const [installJobId, setInstallJobId] = useState<string | null>(null)
  const { job, latest } = useJobProgress(installJobId)

  const refresh = (): void => {
    void api
      .listModels()
      .then((result) => setModels(result.models))
      .catch(setError)
  }

  useEffect(refresh, [])

  // Refresh the list once an install finishes.
  useEffect(() => {
    if (job && (job.status === 'completed' || job.status === 'failed')) {
      refresh()
      if (job.status === 'completed') setInstallJobId(null)
    }
  }, [job])

  const handleRequestInstall = async (modelId: string): Promise<void> => {
    setError(null)
    try {
      setPlan(await api.planInstall(modelId))
    } catch (caught) {
      setError(caught)
    }
  }

  const handleConfirmInstall = async (): Promise<void> => {
    if (!plan) return
    try {
      const started = await api.installModel(plan.model_id)
      setInstallJobId(started.id)
      setPlan(null)
    } catch (caught) {
      setError(caught)
      setPlan(null)
    }
  }

  const handleRemove = async (model: ModelInfo): Promise<void> => {
    if (!window.confirm(`Remove ${model.name}? You can download it again later.`)) return
    try {
      await api.removeModel(model.id)
      refresh()
    } catch (caught) {
      setError(caught)
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Models</h1>
        <p>
          Dabuj ships no model weights. Models are downloaded only when you ask, and
          each carries its own license.
        </p>
      </div>

      <ErrorBox error={error} />

      {plan ? (
        <div className="card" role="dialog" aria-label="Confirm download">
          <h2>Download {plan.name}?</h2>
          <table style={{ marginBottom: '0.75rem' }}>
            <tbody>
              <tr>
                <th>Size</th>
                <td>
                  <strong>{formatBytes(plan.total_bytes)}</strong> in {plan.file_count} file
                  {plan.file_count === 1 ? '' : 's'}
                </td>
              </tr>
              <tr>
                <th>Source</th>
                <td className="mono">{plan.source}</td>
              </tr>
              <tr>
                <th>License</th>
                <td>{plan.license}</td>
              </tr>
              <tr>
                <th>Verified</th>
                <td>
                  {plan.verifiable_bytes >= plan.total_bytes
                    ? 'Every file has a published checksum'
                    : `${formatBytes(plan.verifiable_bytes)} checksum-verified, the rest by size`}
                </td>
              </tr>
            </tbody>
          </table>
          <div className="row">
            <button className="primary" onClick={() => void handleConfirmInstall()}>
              Download
            </button>
            <button onClick={() => setPlan(null)}>Cancel</button>
          </div>
        </div>
      ) : null}

      {installJobId && job ? (
        <div className="card">
          <h2>{job.title}</h2>
          <ProgressBar value={latest?.progress ?? job.overall_progress} />
          <div className="row" style={{ marginTop: '0.5rem' }}>
            <span className="small muted">{latest?.message ?? job.status}</span>
            <button style={{ marginLeft: 'auto' }} onClick={() => void api.cancelJob(job.id)}>
              Cancel
            </button>
          </div>
        </div>
      ) : null}

      {models === null ? (
        <Loading what="Loading catalog" />
      ) : (
        <div className="card card--flush">
          <table>
            <thead>
              <tr>
                <th>Model</th>
                <th>Size</th>
                <th>License</th>
                <th>Status</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {models.map((model) => (
                <tr key={model.id}>
                  <td>
                    <strong>{model.name}</strong>
                    <div className="small muted">{model.description}</div>
                    {model.model_card ? (
                      <a
                        className="small"
                        href={model.model_card}
                        target="_blank"
                        rel="noreferrer noopener"
                      >
                        Model card
                      </a>
                    ) : null}
                  </td>
                  <td className="small">
                    {model.installed
                      ? formatBytes(model.installed_size_bytes)
                      : model.approx_size_label}
                  </td>
                  <td className="small">
                    {model.license}
                    <div className="muted">
                      {model.commercial_use ? 'Commercial use allowed' : 'Check terms'}
                    </div>
                  </td>
                  <td className="small">
                    {model.installed ? (
                      <span style={{ color: 'var(--success)' }}>Installed</span>
                    ) : (
                      <span className="muted">Not installed</span>
                    )}
                  </td>
                  <td style={{ textAlign: 'right' }}>
                    {model.installed ? (
                      <button className="danger" onClick={() => void handleRemove(model)}>
                        Remove
                      </button>
                    ) : (
                      <button onClick={() => void handleRequestInstall(model.id)}>
                        Download…
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="small muted">
        The MIT license of Dabuj itself covers the source code only. Check each
        model&apos;s license before using its output commercially.
      </p>
    </>
  )
}
