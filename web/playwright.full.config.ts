// Full loop: real engine (mocked feed, stub model) -> local Postgres (real schema) -> web app.
//   npm run e2e:full      (needs the Python venv; override with NATGAS_PYTHON=/path/to/python)
import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { defineConfig, devices } from "@playwright/test";

const channel = process.env.PW_CHANNEL;
const DB_PORT = 54330;
const DATABASE_URL = `postgresql://postgres:demo@127.0.0.1:${DB_PORT}/postgres?sslmode=disable`;
const APP = "http://localhost:3198";
const venv = ["../.venv/Scripts/python.exe", "../.venv/bin/python"].map((p) => resolve(p)).find(existsSync);
const python = process.env.NATGAS_PYTHON ?? venv ?? "python";

export default defineConfig({
  testDir: "e2e-full",
  timeout: 240_000,
  expect: { timeout: 60_000 },
  workers: 1,
  reporter: [["list"]],
  use: { ...devices["Desktop Chrome"], baseURL: APP, viewport: { width: 1440, height: 900 }, ...(channel ? { channel } : {}) },
  webServer: [
    { command: `node mock/serve.mjs --port ${DB_PORT}`, port: DB_PORT, reuseExistingServer: false, timeout: 60_000 },
    {
      // 24 Nov 2026 is an ordinary Tuesday; the clock runs 120x so a candle closes every 7.5 s.
      command: `"${python}" -m engine.simulate --serve --speed 120 --seed 7 --agent reckless --start 2026-11-24T09:40:00 --database-url "${DATABASE_URL}"`,
      cwd: "..",
      // the engine has no port to wait for; wait for its first line
      stdout: "pipe",
      wait: { stdout: /mocked feed from/ },
      reuseExistingServer: false,
      timeout: 60_000,
    },
    {
      command: "npx next dev -p 3198",
      url: `${APP}/login`,
      reuseExistingServer: false,
      timeout: 180_000,
      env: {
        DATABASE_URL,
        APP_PASSWORD: "demo1234",
        SESSION_SECRET: "test-only-secret-test-only-secret-test",
        NEXT_TELEMETRY_DISABLED: "1",
      },
    },
  ],
});
