/**
 * Live job progress over WebSocket.
 *
 * Falls back to polling if the socket cannot be opened, so progress keeps
 * updating even behind a proxy that mishandles WebSocket upgrades. The
 * fallback is a real fallback -- it produces the same shape of data, so
 * callers cannot tell the difference.
 */

import { useEffect, useRef, useState } from 'react'
import { api, jobSocketUrl } from '../api/client'
import type { Job, ProgressEvent } from '../api/types'

const POLL_INTERVAL_MS = 1000

export interface JobProgress {
  job: Job | null
  latest: ProgressEvent | null
  connected: boolean
}

export function useJobProgress(jobId: string | null): JobProgress {
  const [job, setJob] = useState<Job | null>(null)
  const [latest, setLatest] = useState<ProgressEvent | null>(null)
  const [connected, setConnected] = useState(false)
  const pollTimer = useRef<number | null>(null)

  useEffect(() => {
    if (!jobId) {
      setJob(null)
      setLatest(null)
      return
    }

    let cancelled = false
    let socket: WebSocket | null = null

    const stopPolling = (): void => {
      if (pollTimer.current !== null) {
        window.clearInterval(pollTimer.current)
        pollTimer.current = null
      }
    }

    const startPolling = (): void => {
      if (pollTimer.current !== null) return
      pollTimer.current = window.setInterval(() => {
        void api
          .getJob(jobId)
          .then((next) => {
            if (cancelled) return
            setJob(next)
            if (next.status !== 'running' && next.status !== 'queued') stopPolling()
          })
          .catch(() => stopPolling())
      }, POLL_INTERVAL_MS)
    }

    try {
      socket = new WebSocket(jobSocketUrl(jobId))
    } catch {
      startPolling()
      return () => {
        cancelled = true
        stopPolling()
      }
    }

    socket.onopen = () => {
      if (!cancelled) setConnected(true)
    }

    socket.onmessage = (message) => {
      if (cancelled) return
      try {
        const payload = JSON.parse(message.data as string) as
          | { type: 'snapshot' | 'final'; job: Job }
          | { type: 'progress'; event: ProgressEvent }
        if (payload.type === 'progress') {
          setLatest(payload.event)
        } else {
          setJob(payload.job)
        }
      } catch {
        // A malformed frame is not worth tearing the connection down for.
      }
    }

    socket.onerror = () => {
      if (!cancelled) startPolling()
    }

    socket.onclose = () => {
      if (cancelled) return
      setConnected(false)
      // Make sure the final state is captured even if the socket dropped
      // before the `final` frame arrived.
      void api
        .getJob(jobId)
        .then((next) => !cancelled && setJob(next))
        .catch(() => undefined)
    }

    return () => {
      cancelled = true
      stopPolling()
      socket?.close()
    }
  }, [jobId])

  return { job, latest, connected }
}
