import react from '@vitejs/plugin-react'
import { loadEnv } from 'vite'
import { defineConfig } from 'vitest/config'

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  server: { proxy: { '/api': loadEnv(mode, process.cwd(), 'VITE_API_PROXY_TARGET').VITE_API_PROXY_TARGET || 'http://127.0.0.1:8000' } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    // RPi4에서 동시에 실행하는 jsdom 환경의 CPU 경쟁을 줄인다.
    maxWorkers: process.env.CI === 'true' ? 2 : undefined,
  },
}))
