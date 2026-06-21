import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// vitest/config's defineConfig is a superset of vite's, so one file serves both the
// dev/build pipeline and the unit-test runner.
export default defineConfig({
  plugins: [react()],
  server: { port: 5173, host: '127.0.0.1' },
  test: {
    environment: 'jsdom',
    globals: false,
    setupFiles: ['./src/test-setup.ts'],
    // Unit tests live under src/; e2e/ is Playwright's, not vitest's.
    include: ['src/**/*.{test,spec}.{ts,tsx}'],
  },
})
