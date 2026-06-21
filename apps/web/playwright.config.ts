import { defineConfig, devices } from '@playwright/test'

// Boots the Vite dev server and drives the app shell. The full suite is human/CI-only
// (`mise run test:e2e`); the agent gate runs the changed-spec subset (`test:e2e:changed`).
export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  reporter: 'list',
  use: { baseURL: 'http://127.0.0.1:5173' },
  webServer: {
    command: 'pnpm run dev',
    url: 'http://127.0.0.1:5173',
    reuseExistingServer: true,
    timeout: 60_000,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
