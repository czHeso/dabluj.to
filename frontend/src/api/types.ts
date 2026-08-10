/**
 * Types mirroring the backend's wire format.
 *
 * Hand-written rather than generated: the API surface is small, and an
 * explicit contract makes it obvious when the backend changes shape.
 */

export type QualityProfile = 'low' | 'balanced' | 'high' | 'ultra'
export type Device = 'auto' | 'cpu' | 'cuda' | 'directml' | 'rocm' | 'metal'

export type JobStatus = 'queued' | 'running' | 'completed' | 'failed' | 'cancelled'
export type StageState =
  | 'pending'
  | 'running'
  | 'completed'
  | 'failed'
  | 'cancelled'
  | 'skipped'

/** The single error shape every failing endpoint returns. */
export interface ApiErrorPayload {
  code: string
  summary: string
  reason?: string | null
  suggestions: string[]
  context?: Record<string, unknown>
}

export interface Word {
  text: string
  start: number
  end: number
  confidence: number | null
}

export interface Translation {
  language: string
  text: string
  adapted_text: string | null
}

export interface Segment {
  id: string
  start: number
  end: number
  raw_text: string
  edited_text: string | null
  speaker_id: string | null
  confidence: number | null
  words: Word[]
  translations: Record<string, Translation>
}

export interface Transcript {
  segments: Segment[]
  language: string | null
  language_confidence: number | null
  duration: number | null
}

export interface Speaker {
  id: string
  display_name: string | null
  voice_id: string | null
}

export interface ProjectSummary {
  id: string
  name: string
  created_at: number
  updated_at: number
  source_filename: string
  segment_count: number
  language: string | null
  completed_stages: string[]
}

export interface ProjectDocument {
  id: string
  name: string
  schema_version: number
  transcript: Transcript
  speakers: Record<string, Speaker>
  warnings: string[]
  source: { original_filename: string; size_bytes: number }
}

export interface ModelInfo {
  id: string
  name: string
  task: string
  description: string
  approx_size_bytes: number
  approx_size_label: string
  languages: string[]
  license: string
  license_url: string | null
  commercial_use: boolean
  model_card: string | null
  supported_devices: string[]
  installed: boolean
  installed_size_bytes: number | null
}

export interface InstallPlan {
  model_id: string
  name: string
  total_bytes: number
  verifiable_bytes: number
  file_count: number
  license: string
  source: string
}

export interface JobStage {
  stage: string
  label: string
  state: StageState
  fraction: number | null
  message: string | null
}

export interface Job {
  id: string
  kind: string
  title: string
  status: JobStatus
  project_id: string | null
  overall_progress: number | null
  error: ApiErrorPayload | null
  stages: JobStage[]
}

export interface ProgressEvent {
  job_id: string
  status: JobStatus
  stage: string | null
  progress: number | null
  overall_progress: number | null
  message: string | null
  eta_seconds: number | null
  elapsed_seconds: number | null
  realtime_factor: number | null
  device: string | null
  model_id: string | null
}

export interface SystemReport {
  application: { name: string; version: string }
  system: {
    os: { name: string; version: string; machine: string }
    cpu: { name: string; physical_cores: number | null; logical_cores: number | null }
    memory: { total_gib: number; available_gib: number }
    gpus: Array<{ name: string; total_memory_gib: number | null }>
    accelerators: { best_device: string; cuda: boolean }
    free_disk_gib: number | null
  }
  recommendation: {
    profile: QualityProfile
    label: string
    description: string
    device: string
    precision: string
    reasons: string[]
    warnings: string[]
  }
  ffmpeg: { available: boolean; version: string | null }
  storage: { models_dir: string; projects_dir: string; installed_models: number }
  privacy: { telemetry: boolean; cloud_providers_allowed: boolean }
}

export interface HealthCheck {
  name: string
  ok: boolean
  detail: string
  suggestion: string | null
}
