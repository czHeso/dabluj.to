/**
 * The single place the frontend talks to the backend.
 *
 * No component calls `fetch` directly. That keeps error handling in one place
 * and makes the whole API surface visible from one file.
 */

import type {
  ApiErrorPayload,
  HealthCheck,
  InstallPlan,
  Job,
  ModelInfo,
  ProjectDocument,
  ProjectSummary,
  Segment,
  Speaker,
  SystemReport,
  Transcript,
} from './types'

/**
 * An error carrying the backend's structured payload.
 *
 * The UI renders `summary`, `reason` and `suggestions` rather than a raw
 * status code, so a user always gets something actionable.
 */
export class ApiError extends Error {
  readonly payload: ApiErrorPayload
  readonly status: number

  constructor(payload: ApiErrorPayload, status: number) {
    super(payload.summary)
    this.name = 'ApiError'
    this.payload = payload
    this.status = status
  }

  get suggestions(): string[] {
    return this.payload.suggestions ?? []
  }
}

const NETWORK_ERROR: ApiErrorPayload = {
  code: 'network_error',
  summary: 'Could not reach the Dabuj backend.',
  reason: 'The local server is not responding.',
  suggestions: ['Check that Dabuj is still running in your terminal'],
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response
  try {
    response = await fetch(path, {
      ...init,
      headers: {
        ...(init?.body ? { 'Content-Type': 'application/json' } : {}),
        ...init?.headers,
      },
    })
  } catch {
    throw new ApiError(NETWORK_ERROR, 0)
  }

  if (!response.ok) {
    let payload: ApiErrorPayload
    try {
      payload = (await response.json()) as ApiErrorPayload
    } catch {
      payload = {
        code: 'unexpected_response',
        summary: `The server returned an unexpected ${response.status} response.`,
        suggestions: [],
      }
    }
    throw new ApiError(payload, response.status)
  }

  if (response.status === 204) return undefined as T
  return (await response.json()) as T
}

const post = <T>(path: string, body?: unknown): Promise<T> =>
  request<T>(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })

export const api = {
  // -- system ------------------------------------------------------------
  health: () => request<{ status: string; version: string }>('/api/health'),
  system: () => request<SystemReport>('/api/system'),
  checks: () => request<{ ok: boolean; checks: HealthCheck[] }>('/api/system/checks'),

  // -- models ------------------------------------------------------------
  listModels: (task?: string) =>
    request<{ models: ModelInfo[] }>(`/api/models${task ? `?task=${task}` : ''}`),
  planInstall: (modelId: string) => request<InstallPlan>(`/api/models/${modelId}/plan`),
  installModel: (modelId: string) => post<Job>('/api/models/install', { model_id: modelId }),
  removeModel: (modelId: string) =>
    request<{ removed: string }>(`/api/models/${modelId}`, { method: 'DELETE' }),

  // -- projects ----------------------------------------------------------
  listProjects: () => request<{ projects: ProjectSummary[] }>('/api/projects'),
  getProject: (id: string) => request<ProjectDocument>(`/api/projects/${id}`),
  createProject: (input: {
    source_path: string
    name?: string
    source_language?: string
    quality?: string
  }) => post<ProjectDocument>('/api/projects', input),
  deleteProject: (id: string) =>
    request<{ deleted: string }>(`/api/projects/${id}`, { method: 'DELETE' }),

  // -- transcript --------------------------------------------------------
  getTranscript: (id: string) =>
    request<{ transcript: Transcript; speakers: Record<string, Speaker> }>(
      `/api/projects/${id}/transcript`,
    ),
  updateSegment: (
    projectId: string,
    segmentId: string,
    changes: { text?: string; speaker_id?: string; start?: number; end?: number },
  ) =>
    request<Segment>(`/api/projects/${projectId}/segments/${segmentId}`, {
      method: 'PATCH',
      body: JSON.stringify(changes),
    }),
  splitSegment: (projectId: string, segmentId: string, timestamp: number) =>
    post<Transcript>(`/api/projects/${projectId}/segments/${segmentId}/split`, { timestamp }),
  mergeSegments: (projectId: string, firstId: string, secondId: string) =>
    post<Transcript>(`/api/projects/${projectId}/segments/merge`, {
      first_id: firstId,
      second_id: secondId,
    }),
  renameSpeaker: (projectId: string, speakerId: string, displayName: string | null) =>
    request<Speaker>(`/api/projects/${projectId}/speakers/${speakerId}`, {
      method: 'PATCH',
      body: JSON.stringify({ display_name: displayName }),
    }),

  // -- jobs --------------------------------------------------------------
  listJobs: () => request<{ jobs: Job[] }>('/api/jobs'),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  cancelJob: (id: string) => post<Job>(`/api/jobs/${id}/cancel`),
  startTranscription: (input: {
    project_id: string
    language?: string
    model_id?: string
    device?: string
    force?: boolean
  }) => post<Job>('/api/jobs/transcribe', input),

  // -- export ------------------------------------------------------------
  exportTranscript: (projectId: string, format: string) =>
    post<{ path: string; bytes: number }>(`/api/projects/${projectId}/export`, { format }),
  downloadUrl: (projectId: string, format: string) =>
    `/api/projects/${projectId}/export/${format}`,
}

/** WebSocket URL for a job's progress feed, on the page's own origin. */
export function jobSocketUrl(jobId: string): string {
  const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${protocol}//${window.location.host}/ws/jobs/${jobId}`
}
