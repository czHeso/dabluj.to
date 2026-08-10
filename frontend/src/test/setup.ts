import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// jsdom has no WebSocket. Tests that care about progress assert on the polling
// fallback instead, so a minimal stub is enough here.
class WebSocketStub {
  static readonly OPEN = 1
  onopen: (() => void) | null = null
  onmessage: ((event: { data: string }) => void) | null = null
  onerror: (() => void) | null = null
  onclose: (() => void) | null = null
  close(): void {
    this.onclose?.()
  }
}

vi.stubGlobal('WebSocket', WebSocketStub)
