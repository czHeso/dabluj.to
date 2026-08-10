// `defineConfig` comes from vitest/config rather than vite so that the `test`
// block below is type-checked rather than rejected as an unknown option.
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'
import path from 'node:path'

// The dev server proxies to the Python backend so that the browser sees a
// single origin during development, which keeps the API's origin checks happy
// and means no CORS special-casing is needed outside `security.py`.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(__dirname, './src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:7860', changeOrigin: false },
      '/ws': { target: 'ws://127.0.0.1:7860', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: true,
  },
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
})
