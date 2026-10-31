import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './tests',
  fullyParallel: true,
  use: { baseURL: 'http://127.0.0.1:4375', trace: 'retain-on-failure' },
  webServer: {
    command: 'npm run preview -- --port 4375 --strictPort',
    url: 'http://127.0.0.1:4375',
    reuseExistingServer: false,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
