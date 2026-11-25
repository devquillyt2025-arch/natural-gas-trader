// npm run claude:check - one real request to Claude on made-up prices, to prove the set-up in engine/.env works.
import { spawnSync } from "node:child_process";
import { existsSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PYTHON = path.join(ROOT, ".venv", ...(process.platform === "win32" ? ["Scripts", "python.exe"] : ["bin", "python"]));

if (!existsSync(PYTHON)) {
  console.error("The Python environment (.venv) is missing. See README.md.");
  process.exit(1);
}
const done = spawnSync(PYTHON, ["-m", "engine", "claude"], {
  cwd: ROOT,
  stdio: "inherit",
  env: { ...process.env, PYTHONIOENCODING: "utf-8" },
});
process.exit(done.status ?? 1);
