import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': 'http://127.0.0.1:8000' } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    // RPi4では複数のjsdom環境によるCPU競合を抑える。
    maxWorkers: process.env.CI === 'true' ? 2 : undefined,
  },
})
