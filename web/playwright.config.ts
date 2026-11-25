import { defineConfig, devices } from "@playwright/test";

// Uses an installed browser if PW_CHANNEL is set (e.g. PW_CHANNEL=msedge), otherwise Playwright's Chromium.
const channel = process.env.PW_CHANNEL;
const DB_PORT = 54329;
const APP = "http://localhost:3199";

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: APP, trace: "retain-on-failure", ...(channel ? { channel } : {}) },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 }, ...(channel ? { channel } : {}) } },
    { name: "phone", use: { ...devices["Pixel 7"], ...(channel ? { channel } : {}) } },
  ],
  webServer: [
    { command: `node mock/serve.mjs --seed --port ${DB_PORT}`, port: DB_PORT, reuseExistingServer: false, timeout: 60_000 },
    {
      command: "npx next dev -p 3199",
      url: `${APP}/login`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        DATABASE_URL: `postgresql://postgres:demo@127.0.0.1:${DB_PORT}/postgres?sslmode=disable`,
        APP_PASSWORD: "demo1234",
        SESSION_SECRET: "test-only-secret-test-only-secret-test",
        UPSTOX_CLIENT_ID: "mock-client",
        UPSTOX_REDIRECT_URI: `${APP}/api/upstox/callback`,
        NEXT_TELEMETRY_DISABLED: "1",
      },
    },
  ],
});
