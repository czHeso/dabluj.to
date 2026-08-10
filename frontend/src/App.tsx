/**
 * The application shell.
 *
 * Routing is a small piece of state rather than a router dependency: there are
 * five views and no URLs worth deep-linking in a local single-user tool. If
 * that changes, this is the one place to swap.
 */

import { useEffect, useState } from 'react'
import { api } from './api/client'
import type { SystemReport } from './api/types'
import { ErrorBox } from './components/common'
import { ModelsPage } from './pages/ModelsPage'
import { ProcessingPage } from './pages/ProcessingPage'
import { ProjectsPage } from './pages/ProjectsPage'
import { SystemPage } from './pages/SystemPage'
import { TranscriptPage } from './pages/TranscriptPage'

type View =
  | { name: 'projects' }
  | { name: 'processing'; jobId: string }
  | { name: 'transcript'; projectId: string }
  | { name: 'models' }
  | { name: 'system' }

export function App(): JSX.Element {
  const [view, setView] = useState<View>({ name: 'projects' })
  const [system, setSystem] = useState<SystemReport | null>(null)
  const [error, setError] = useState<unknown>(null)

  useEffect(() => {
    void api.system().then(setSystem).catch(setError)
  }, [])

  const isLocalOnly = system ? !system.privacy.cloud_providers_allowed : true

  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">Dabuj</span>

        <nav className="nav">
          <button
            onClick={() => setView({ name: 'projects' })}
            aria-current={view.name === 'projects' ? 'page' : undefined}
          >
            Projects
          </button>
          <button
            onClick={() => setView({ name: 'models' })}
            aria-current={view.name === 'models' ? 'page' : undefined}
          >
            Models
          </button>
          <button
            onClick={() => setView({ name: 'system' })}
            aria-current={view.name === 'system' ? 'page' : undefined}
          >
            System
          </button>
        </nav>

        {/* The privacy state is always visible, never buried in settings. */}
        <span className={`privacy ${isLocalOnly ? 'privacy--local' : 'privacy--cloud'}`}>
          {isLocalOnly
            ? 'Local processing — your media stays here'
            : 'Cloud providers enabled'}
        </span>
      </header>

      <main className="main">
        <ErrorBox error={error} />

        {view.name === 'projects' ? (
          <ProjectsPage
            system={system}
            onOpenProject={(projectId) => setView({ name: 'transcript', projectId })}
            onJobStarted={(jobId) => setView({ name: 'processing', jobId })}
          />
        ) : null}

        {view.name === 'processing' ? (
          <ProcessingPage
            jobId={view.jobId}
            onOpenProject={(projectId) => setView({ name: 'transcript', projectId })}
          />
        ) : null}

        {view.name === 'transcript' ? (
          <TranscriptPage
            projectId={view.projectId}
            onBack={() => setView({ name: 'projects' })}
          />
        ) : null}

        {view.name === 'models' ? <ModelsPage /> : null}
        {view.name === 'system' ? <SystemPage system={system} /> : null}
      </main>
    </div>
  )
}
