/** Home: existing projects, and the form that creates a new one. */

import { useEffect, useState } from 'react'
import { api } from '../api/client'
import type { ProjectSummary, SystemReport } from '../api/types'
import { Empty, ErrorBox, Loading, formatTime } from '../components/common'

interface Props {
  onOpenProject: (projectId: string) => void
  onJobStarted: (jobId: string) => void
  system: SystemReport | null
}

export function ProjectsPage({ onOpenProject, onJobStarted, system }: Props): JSX.Element {
  const [projects, setProjects] = useState<ProjectSummary[] | null>(null)
  const [error, setError] = useState<unknown>(null)
  const [sourcePath, setSourcePath] = useState('')
  const [language, setLanguage] = useState('auto')
  const [busy, setBusy] = useState(false)

  const refresh = (): void => {
    void api
      .listProjects()
      .then((result) => setProjects(result.projects))
      .catch(setError)
  }

  useEffect(refresh, [])

  const handleCreate = async (event: React.FormEvent): Promise<void> => {
    event.preventDefault()
    setError(null)
    setBusy(true)
    try {
      const project = await api.createProject({
        source_path: sourcePath.trim(),
        source_language: language,
      })
      const job = await api.startTranscription({
        project_id: project.id,
        ...(language !== 'auto' ? { language } : {}),
      })
      setSourcePath('')
      onJobStarted(job.id)
    } catch (caught) {
      setError(caught)
    } finally {
      setBusy(false)
    }
  }

  const handleDelete = async (projectId: string, name: string): Promise<void> => {
    if (!window.confirm(`Delete the project "${name}" and everything in it?`)) return
    try {
      await api.deleteProject(projectId)
      refresh()
    } catch (caught) {
      setError(caught)
    }
  }

  return (
    <>
      <div className="page-header">
        <h1>Projects</h1>
        <p>Transcribe a media file. Nothing leaves this computer.</p>
      </div>

      <ErrorBox error={error} />

      <form className="card" onSubmit={handleCreate}>
        <h2>New project</h2>
        <div className="stack">
          <div>
            <label htmlFor="source-path">Media file</label>
            <input
              id="source-path"
              type="text"
              value={sourcePath}
              onChange={(event) => setSourcePath(event.target.value)}
              placeholder="C:\Users\you\Videos\interview.mkv"
              required
              spellCheck={false}
            />
            <p className="small muted" style={{ margin: '0.35rem 0 0' }}>
              The full path to a file on this computer. Dabuj reads it directly, so
              even a multi-gigabyte video is not uploaded anywhere.
            </p>
          </div>

          <div className="row">
            <div style={{ minWidth: '12rem' }}>
              <label htmlFor="language">Source language</label>
              <select
                id="language"
                value={language}
                onChange={(event) => setLanguage(event.target.value)}
              >
                <option value="auto">Auto detect</option>
                <option value="en">English</option>
                <option value="de">German</option>
                <option value="cs">Czech</option>
                <option value="sk">Slovak</option>
                <option value="pl">Polish</option>
                <option value="es">Spanish</option>
                <option value="fr">French</option>
              </select>
            </div>

            {system ? (
              <div style={{ minWidth: '12rem' }}>
                <label>Quality</label>
                <p className="small muted" style={{ margin: 0 }}>
                  {system.recommendation.label} — recommended for this computer
                </p>
              </div>
            ) : null}
          </div>

          <div>
            <button className="primary" type="submit" disabled={busy || !sourcePath.trim()}>
              {busy ? 'Starting…' : 'Transcribe'}
            </button>
          </div>
        </div>
      </form>

      <h2>Your projects</h2>
      {projects === null ? (
        <Loading what="Loading projects" />
      ) : projects.length === 0 ? (
        <Empty
          title="No projects yet"
          hint="Add a media file above to create your first one."
        />
      ) : (
        <div className="card card--flush">
          <table>
            <thead>
              <tr>
                <th>Name</th>
                <th>Source</th>
                <th>Segments</th>
                <th>Language</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {projects.map((project) => (
                <tr key={project.id}>
                  <td>
                    <button
                      style={{
                        border: 'none',
                        background: 'none',
                        padding: 0,
                        color: 'var(--accent)',
                        cursor: 'pointer',
                      }}
                      onClick={() => onOpenProject(project.id)}
                    >
                      {project.name}
                    </button>
                    <div className="small muted">
                      Updated {formatRelative(project.updated_at)}
                    </div>
                  </td>
                  <td className="small muted">{project.source_filename}</td>
                  <td>{project.segment_count || '—'}</td>
                  <td>{project.language ?? '—'}</td>
                  <td style={{ textAlign: 'right' }}>
                    <button
                      className="danger"
                      onClick={() => void handleDelete(project.id, project.name)}
                    >
                      Delete
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  )
}

function formatRelative(epochSeconds: number): string {
  const elapsed = Date.now() / 1000 - epochSeconds
  if (elapsed < 60) return 'just now'
  if (elapsed < 3600) return `${Math.floor(elapsed / 60)} min ago`
  if (elapsed < 86400) return `${Math.floor(elapsed / 3600)} h ago`
  return `${Math.floor(elapsed / 86400)} d ago`
}

export { formatRelative, formatTime }
