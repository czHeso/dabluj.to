/**
 * Frontend behaviour tests.
 *
 * These cover what actually matters to a user: that the privacy state is
 * visible, that errors are rendered usefully rather than as status codes, that
 * editing a transcript preserves the original, and that a download is never
 * started without showing its size first.
 */

import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { App } from '../App'
import { ErrorBox, formatBytes, formatPreciseTime, formatTime } from '../components/common'
import { ModelsPage } from '../pages/ModelsPage'
import { TranscriptPage } from '../pages/TranscriptPage'
import { api, ApiError } from '../api/client'
import type { ModelInfo, ProjectDocument, SystemReport, Transcript } from '../api/types'

const systemReport: SystemReport = {
  application: { name: 'Dabuj', version: '0.1.0' },
  system: {
    os: { name: 'Windows', version: '11', machine: 'AMD64' },
    cpu: { name: 'Test CPU', physical_cores: 8, logical_cores: 16 },
    memory: { total_gib: 32, available_gib: 20 },
    gpus: [{ name: 'Test GPU', total_memory_gib: 8 }],
    accelerators: { best_device: 'cuda', cuda: true },
    free_disk_gib: 200,
  },
  recommendation: {
    profile: 'high',
    label: 'High',
    description: 'Larger models on a capable GPU.',
    device: 'cuda',
    precision: 'float16',
    reasons: ['8 GB of VRAM is enough for a large model on the GPU.'],
    warnings: [],
  },
  ffmpeg: { available: true, version: 'ffmpeg 9.0' },
  storage: { models_dir: '/models', projects_dir: '/projects', installed_models: 1 },
  privacy: { telemetry: false, cloud_providers_allowed: false },
}

beforeEach(() => {
  vi.spyOn(api, 'system').mockResolvedValue(systemReport)
  vi.spyOn(api, 'listProjects').mockResolvedValue({ projects: [] })
})

describe('formatters', () => {
  it('formats durations', () => {
    expect(formatTime(0)).toBe('0:00')
    expect(formatTime(61)).toBe('1:01')
    expect(formatTime(3661)).toBe('1:01:01')
    expect(formatTime(null)).toBe('—')
  })

  it('formats precise timestamps for the editor', () => {
    expect(formatPreciseTime(83.456)).toBe('1:23.45')
  })

  it('formats byte counts', () => {
    expect(formatBytes(1024)).toBe('1 KB')
    expect(formatBytes(5 * 1024 * 1024)).toBe('5.0 MB')
    expect(formatBytes(0)).toBe('—')
  })
})

describe('ErrorBox', () => {
  it('renders the summary, reason and suggestions rather than a status code', () => {
    const error = new ApiError(
      {
        code: 'insufficient_resources',
        summary: 'Not enough GPU memory.',
        reason: 'The model needs 8 GB but 5.7 GB is free.',
        suggestions: ['Switch to Balanced', 'Use CPU mode'],
      },
      507,
    )

    render(<ErrorBox error={error} />)

    expect(screen.getByRole('alert')).toBeInTheDocument()
    expect(screen.getByText('Not enough GPU memory.')).toBeInTheDocument()
    expect(screen.getByText(/needs 8 GB/)).toBeInTheDocument()
    expect(screen.getByText('Switch to Balanced')).toBeInTheDocument()
    expect(screen.getByText('Use CPU mode')).toBeInTheDocument()
  })

  it('renders a plain job error payload identically', () => {
    render(
      <ErrorBox
        error={{ payload: { code: 'x', summary: 'Job failed.', suggestions: ['Retry'] } }}
      />,
    )

    expect(screen.getByText('Job failed.')).toBeInTheDocument()
    expect(screen.getByText('Retry')).toBeInTheDocument()
  })

  it('renders nothing when there is no error', () => {
    const { container } = render(<ErrorBox error={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})

describe('App shell', () => {
  it('shows that processing is local', async () => {
    render(<App />)

    expect(
      await screen.findByText(/your media stays here/i),
    ).toBeInTheDocument()
  })

  it('warns when cloud providers are enabled', async () => {
    vi.spyOn(api, 'system').mockResolvedValue({
      ...systemReport,
      privacy: { telemetry: false, cloud_providers_allowed: true },
    })

    render(<App />)

    expect(await screen.findByText(/cloud providers enabled/i)).toBeInTheDocument()
  })

  it('navigates between views', async () => {
    vi.spyOn(api, 'listModels').mockResolvedValue({ models: [] })
    const user = userEvent.setup()
    render(<App />)

    await user.click(screen.getByRole('button', { name: 'Models' }))

    expect(await screen.findByRole('heading', { name: 'Models' })).toBeInTheDocument()
  })

  it('shows an empty state when there are no projects', async () => {
    render(<App />)
    expect(await screen.findByText(/no projects yet/i)).toBeInTheDocument()
  })
})

describe('ModelsPage', () => {
  const model: ModelInfo = {
    id: 'whisper-small',
    name: 'Whisper Small',
    task: 'asr',
    description: 'A good default.',
    approx_size_bytes: 484 * 1024 * 1024,
    approx_size_label: '~484 MB',
    languages: ['en', 'de', 'cs'],
    license: 'MIT',
    license_url: null,
    commercial_use: true,
    model_card: 'https://example.invalid/card',
    supported_devices: ['cpu', 'cuda'],
    installed: false,
    installed_size_bytes: null,
  }

  it('lists models with their license', async () => {
    vi.spyOn(api, 'listModels').mockResolvedValue({ models: [model] })

    render(<ModelsPage />)

    expect(await screen.findByText('Whisper Small')).toBeInTheDocument()
    expect(screen.getByText('MIT')).toBeInTheDocument()
    expect(screen.getByText(/commercial use allowed/i)).toBeInTheDocument()
  })

  it('shows the download size before installing anything', async () => {
    vi.spyOn(api, 'listModels').mockResolvedValue({ models: [model] })
    const plan = vi.spyOn(api, 'planInstall').mockResolvedValue({
      model_id: 'whisper-small',
      name: 'Whisper Small',
      total_bytes: 484 * 1024 * 1024,
      verifiable_bytes: 484 * 1024 * 1024,
      file_count: 4,
      license: 'MIT',
      source: 'Systran/faster-whisper-small',
    })
    const install = vi.spyOn(api, 'installModel')

    const user = userEvent.setup()
    render(<ModelsPage />)

    await user.click(await screen.findByRole('button', { name: /download…/i }))

    expect(plan).toHaveBeenCalledWith('whisper-small')
    expect(await screen.findByRole('dialog')).toBeInTheDocument()
    expect(screen.getByText('484.0 MB')).toBeInTheDocument()
    expect(screen.getByText('Systran/faster-whisper-small')).toBeInTheDocument()
    // Nothing downloaded until the user confirms.
    expect(install).not.toHaveBeenCalled()
  })

  it('downloads only after confirmation', async () => {
    vi.spyOn(api, 'listModels').mockResolvedValue({ models: [model] })
    vi.spyOn(api, 'planInstall').mockResolvedValue({
      model_id: 'whisper-small',
      name: 'Whisper Small',
      total_bytes: 100,
      verifiable_bytes: 100,
      file_count: 1,
      license: 'MIT',
      source: 'Systran/faster-whisper-small',
    })
    const install = vi.spyOn(api, 'installModel').mockResolvedValue({
      id: 'job1',
      kind: 'model_install',
      title: 'Installing',
      status: 'queued',
      project_id: null,
      overall_progress: 0,
      error: null,
      stages: [],
    })
    vi.spyOn(api, 'getJob').mockResolvedValue({
      id: 'job1',
      kind: 'model_install',
      title: 'Installing',
      status: 'completed',
      project_id: null,
      overall_progress: 1,
      error: null,
      stages: [],
    })

    const user = userEvent.setup()
    render(<ModelsPage />)

    await user.click(await screen.findByRole('button', { name: /download…/i }))
    await user.click(await screen.findByRole('button', { name: 'Download' }))

    await waitFor(() => expect(install).toHaveBeenCalledWith('whisper-small'))
  })
})

describe('TranscriptPage', () => {
  const project: ProjectDocument = {
    id: 'p1',
    name: 'Interview',
    schema_version: 1,
    warnings: [],
    speakers: {},
    source: { original_filename: 'interview.mkv', size_bytes: 1000 },
    transcript: { segments: [], language: 'en', language_confidence: 0.98, duration: 10 },
  }

  const transcript: Transcript = {
    language: 'en',
    language_confidence: 0.98,
    duration: 10,
    segments: [
      {
        id: 'seg_1',
        start: 0,
        end: 2.5,
        raw_text: 'Hello world',
        edited_text: null,
        speaker_id: null,
        confidence: 0.95,
        words: [],
        translations: {},
      },
      {
        id: 'seg_2',
        start: 2.5,
        end: 5,
        raw_text: 'Uncertain bit',
        edited_text: null,
        speaker_id: null,
        confidence: 0.2,
        words: [],
        translations: {},
      },
    ],
  }

  beforeEach(() => {
    vi.spyOn(api, 'getProject').mockResolvedValue(project)
    vi.spyOn(api, 'getTranscript').mockResolvedValue({ transcript, speakers: {} })
  })

  it('renders segments with timestamps', async () => {
    render(<TranscriptPage projectId="p1" onBack={() => {}} />)

    expect(await screen.findByDisplayValue('Hello world')).toBeInTheDocument()
    expect(screen.getByText('0:00.00')).toBeInTheDocument()
  })

  it('flags low-confidence segments for review', async () => {
    render(<TranscriptPage projectId="p1" onBack={() => {}} />)

    expect(
      await screen.findByText(/1 segment had low recognition confidence/i),
    ).toBeInTheDocument()
  })

  it('saves an edit and shows the original alongside it', async () => {
    const update = vi.spyOn(api, 'updateSegment').mockResolvedValue({
      ...transcript.segments[0]!,
      edited_text: 'Hello there',
    })

    const user = userEvent.setup()
    render(<TranscriptPage projectId="p1" onBack={() => {}} />)

    const box = await screen.findByDisplayValue('Hello world')
    await user.clear(box)
    await user.type(box, 'Hello there')
    await user.tab()

    await waitFor(() =>
      expect(update).toHaveBeenCalledWith('p1', 'seg_1', { text: 'Hello there' }),
    )
    // The ASR output is still visible: editing never destroys it.
    expect(await screen.findByText(/Original: Hello world/)).toBeInTheDocument()
    expect(screen.getByText('edited')).toBeInTheDocument()
  })

  it('shows a useful message when loading fails', async () => {
    vi.spyOn(api, 'getProject').mockRejectedValue(
      new ApiError(
        { code: 'not_found', summary: 'No project with that ID.', suggestions: ['Check the list'] },
        404,
      ),
    )

    render(<TranscriptPage projectId="missing" onBack={() => {}} />)

    expect(await screen.findByText('No project with that ID.')).toBeInTheDocument()
    expect(screen.getByText('Check the list')).toBeInTheDocument()
  })
})
