/** Hardware report, installation checks and privacy state. */

import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { HealthCheck, SystemReport } from '../api/types'
import { ErrorBox, Loading } from '../components/common'

export function SystemPage({ system }: { system: SystemReport | null }): JSX.Element {
  const [checks, setChecks] = useState<HealthCheck[] | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    void api
      .checks()
      .then((result) => setChecks(result.checks))
      .catch(setError)
  }, [])

  if (!system) return <Loading what="Inspecting your computer" />

  const { recommendation } = system
  const gpu = system.system.gpus[0]

  return (
    <>
      <div className="page-header">
        <h1>System</h1>
        <p>What Dabuj found on this computer, and what it recommends.</p>
      </div>

      <ErrorBox error={error} />

      <div className="grid">
        <div className="card">
          <h2>Hardware</h2>
          <table>
            <tbody>
              <tr>
                <th>OS</th>
                <td>
                  {system.system.os.name} {system.system.os.version}
                </td>
              </tr>
              <tr>
                <th>CPU</th>
                <td>{system.system.cpu.name}</td>
              </tr>
              <tr>
                <th>Cores</th>
                <td>
                  {system.system.cpu.physical_cores ?? '?'} physical /{' '}
                  {system.system.cpu.logical_cores ?? '?'} logical
                </td>
              </tr>
              <tr>
                <th>Memory</th>
                <td>
                  {system.system.memory.total_gib} GB ({system.system.memory.available_gib} GB
                  free)
                </td>
              </tr>
              <tr>
                <th>GPU</th>
                <td>
                  {gpu
                    ? `${gpu.name}${gpu.total_memory_gib ? ` (${gpu.total_memory_gib} GB)` : ''}`
                    : 'None detected'}
                </td>
              </tr>
              <tr>
                <th>FFmpeg</th>
                <td className="small">{system.ffmpeg.version ?? 'Not found'}</td>
              </tr>
            </tbody>
          </table>
        </div>

        <div className="card">
          <h2>Recommended profile</h2>
          <p>
            <strong>{recommendation.label}</strong>
            <br />
            <span className="small muted">{recommendation.description}</span>
          </p>
          <ul className="small muted" style={{ paddingLeft: '1.1rem' }}>
            {recommendation.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
          {recommendation.warnings.map((warning) => (
            <div key={warning} className="warning small">
              {warning}
            </div>
          ))}
        </div>
      </div>

      <div className="card">
        <h2>Installation checks</h2>
        {checks === null ? (
          <Loading />
        ) : (
          <ul style={{ listStyle: 'none', padding: 0, margin: 0 }}>
            {checks.map((check) => (
              <li key={check.name} style={{ marginBottom: '0.5rem' }}>
                <span
                  aria-hidden="true"
                  style={{
                    color: check.ok ? 'var(--success)' : 'var(--danger)',
                    marginRight: '0.5rem',
                  }}
                >
                  {check.ok ? '✓' : '✗'}
                </span>
                <strong>{check.name}</strong>
                <div className="small muted" style={{ marginLeft: '1.5rem' }}>
                  {check.detail}
                </div>
                {check.suggestion ? (
                  <div
                    className="small"
                    style={{ marginLeft: '1.5rem', color: 'var(--warning)' }}
                  >
                    {check.suggestion}
                  </div>
                ) : null}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="card">
        <h2>Privacy</h2>
        <p className="small">
          Telemetry is <strong>{system.privacy.telemetry ? 'on' : 'off'}</strong>. Cloud
          providers are{' '}
          <strong>{system.privacy.cloud_providers_allowed ? 'allowed' : 'disabled'}</strong>.
        </p>
        <p className="small muted" style={{ margin: 0 }}>
          Projects: <span className="mono">{system.storage.projects_dir}</span>
          <br />
          Models: <span className="mono">{system.storage.models_dir}</span>
        </p>
      </div>
    </>
  )
}
