// One command for NatGas Trader.
//   npm run dev    your real setup if engine/.env and web/.env.local exist (paper unless engine/.env
//                  says otherwise), or the offline demo if neither does. Without the Anthropic and Upstox
//                  keys only the dashboard starts, on your database.
//   npm run demo   always the offline demo: fake prices, a fake trader, a throwaway local database
// First time with Neon: npm run db:setup
// Ctrl+C stops everything.
import { spawn, spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import net from "node:net";
import path from "node:path";
import { createInterface } from "node:readline";
import { setTimeout as sleep } from "node:timers/promises";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const WEB = path.join(ROOT, "web");
const IS_WIN = process.platform === "win32";
const PYTHON = path.join(ROOT, ".venv", ...(IS_WIN ? ["Scripts", "python.exe"] : ["bin", "python"]));
const NEXT = path.join(WEB, "node_modules", "next", "dist", "bin", "next");
const ENGINE_ENV = path.join(ROOT, "engine", ".env");
const WEB_ENV = path.join(WEB, ".env.local");

const DEMO_PASSWORD = "demo1234";

const COLORS = { db: "\x1b[33m", engine: "\x1b[36m", web: "\x1b[35m" };
const running = new Map(); // name -> child process
let stopping = false;

function kill(child) {
  if (IS_WIN) spawnSync("taskkill", ["/pid", String(child.pid), "/T", "/F"], { stdio: "ignore" });
  else child.kill("SIGKILL");
}

function fail(message) {
  stopping = true;
  for (const child of running.values()) kill(child);
  console.error(`\n✖ ${message}\n`);
  process.exit(1);
}

if (!existsSync(PYTHON)) fail("The Python environment (.venv) is missing. See README.md.");
if (!existsSync(NEXT)) fail("The web packages are missing. Run: npm install --prefix web");

const configured = [ENGINE_ENV, WEB_ENV].filter((file) => existsSync(file)).length;
const demo = process.argv.includes("--demo") || configured === 0;
if (!demo && configured === 1) {
  const missing = existsSync(ENGINE_ENV) ? "web/.env.local is missing. Run: npm run db:setup" : "engine/.env is missing (see README.md).";
  fail(`${missing}\n  For the offline demo instead: npm run demo`);
}

/** A value from a settings file, or "" if it is not filled in. Never print what this returns. */
const setting = (file, name) =>
  new RegExp(`^[ \\t]*${name}[ \\t]*=[ \\t]*(.*?)[ \\t]*\\r?$`, "m").exec(readFileSync(file, "utf8"))?.[1] ?? "";
// The engine cannot run without these. A Claude subscription needs no API key.
const subscription = !demo && setting(ENGINE_ENV, "CLAUDE_AUTH").toLowerCase() === "subscription";
const missingKeys = demo
  ? []
  : [...(subscription ? [] : ["ANTHROPIC_API_KEY"]), "UPSTOX_CLIENT_ID", "UPSTOX_CLIENT_SECRET"].filter(
      (name) => !setting(ENGINE_ENV, name),
    );

const connects = (port, host) =>
  new Promise((resolve) => {
    const socket = net.connect({ port, host });
    const done = (result) => {
      socket.destroy();
      resolve(result);
    };
    socket.once("connect", () => done(true));
    socket.once("error", () => done(false));
    socket.setTimeout(1000, () => done(false));
  });
const inUse = async (port) => (await connects(port, "127.0.0.1")) || (await connects(port, "::1"));

async function firstFree(ports) {
  for (const port of ports) if (!(await inUse(port))) return port;
  return fail(`Ports ${ports.join(", ")} are all in use.`);
}

async function chooseWebPort() {
  if (process.env.WEB_PORT) return Number(process.env.WEB_PORT);
  // Once Upstox is set up the port is part of its redirect address, so it must not change by itself.
  if (demo || !setting(WEB_ENV, "UPSTOX_CLIENT_ID")) return firstFree([3000, 3100, 3101, 3102]);
  const redirect = /^\s*UPSTOX_REDIRECT_URI\s*=\s*["']?http:\/\/(?:localhost|127\.0\.0\.1):(\d+)/m.exec(
    readFileSync(WEB_ENV, "utf8"),
  );
  return redirect ? Number(redirect[1]) : 3000;
}

function onExit(name, tag, code) {
  if (stopping) return;
  // A web crash must not take down an engine that may be managing a position.
  if (name === "web" && !demo && running.has("engine")) {
    console.log(`${tag}exited (${code}). The engine is STILL RUNNING. Press Ctrl+C to stop it.`);
    return;
  }
  fail(`${name} exited (${code}), so everything was stopped. The reason is in the lines above.`);
}

function start(name, command, args, { cwd = ROOT, env = {}, errorsOnly = false } = {}) {
  const child = spawn(command, args, {
    cwd,
    env: { ...process.env, PYTHONUNBUFFERED: "1", PYTHONIOENCODING: "utf-8", ...env },
    stdio: ["ignore", "pipe", "pipe"],
  });
  const tag = `${COLORS[name]}[${name}]\x1b[0m `;
  // While the demo shuts down, the engine complains that the demo database closed first. Not worth showing.
  const print = (line) => line.trim() && !(demo && stopping) && console.log(tag + line);
  if (errorsOnly) child.stdout.resume();
  else createInterface({ input: child.stdout }).on("line", print);
  createInterface({ input: child.stderr }).on("line", print);
  running.set(name, child);
  child.on("exit", (code) => {
    running.delete(name);
    // Ctrl+C reaches the children too, so give our own handler a moment to see it first.
    setTimeout(() => onExit(name, tag, code), 500);
  });
}

async function stop() {
  if (stopping) return;
  stopping = true;
  console.log("\nStopping...");
  // Windows delivers Ctrl+C to every process in the terminal; elsewhere, pass it on.
  if (!IS_WIN) for (const child of running.values()) child.kill("SIGINT");
  // The engine gets time to finish cleanly. The others have nothing to save.
  const deadline = Date.now() + 15_000;
  while (running.has("engine") && Date.now() < deadline) await sleep(100);
  for (const child of running.values()) kill(child);
  console.log("Stopped.");
  process.exit(0);
}
for (const signal of ["SIGINT", "SIGTERM", "SIGBREAK", "SIGHUP"]) process.on(signal, stop);

async function waitFor(port, name) {
  for (let i = 0; i < 300 && !stopping; i++) {
    if (await inUse(port)) return;
    await sleep(200);
  }
  if (!stopping) fail(`${name} did not start within 60 seconds.`);
}

const webPort = await chooseWebPort();
if (await inUse(webPort)) {
  fail(`Port ${webPort} is already in use by another app. Stop it, set WEB_PORT, or change the port in both UPSTOX_REDIRECT_URI values.`);
}
const url = `http://localhost:${webPort}`;
const next = [NEXT, "dev", "-p", String(webPort)];

if (demo) {
  const dbPort = await firstFree([54321, 54322, 54323, 54324]);
  const databaseUrl = `postgresql://postgres:demo@127.0.0.1:${dbPort}/postgres?sslmode=disable`;
  console.log("\nDEMO: fake prices, a fake trader and a throwaway database. No Neon, no Claude, no Upstox, no money.\n");
  start("db", process.execPath, ["mock/serve.mjs", "--port", String(dbPort)], { cwd: WEB, errorsOnly: true });
  await waitFor(dbPort, "The demo database");
  const simulate = ["-m", "engine.simulate", "--serve", "--speed", "60", "--agent", "reckless"];
  start("engine", PYTHON, [...simulate, "--database-url", databaseUrl]);
  // These override web/.env.local, so the demo can never touch your real database.
  start("web", process.execPath, next, {
    cwd: WEB,
    env: {
      DATABASE_URL: databaseUrl,
      APP_PASSWORD: DEMO_PASSWORD,
      SESSION_SECRET: "demo-only-secret-demo-only-secret-demo",
      UPSTOX_CLIENT_ID: "",
      UPSTOX_REDIRECT_URI: "",
    },
  });
  await waitFor(webPort, "The web app");
  console.log(`\n  Open      ${url}\n  Password  ${DEMO_PASSWORD}\n\n  Ctrl+C stops everything.\n`);
} else {
  if (missingKeys.length) {
    console.log("\nDASHBOARD ONLY, on your database. THE ENGINE IS NOT RUNNING: nothing is traded or watched.");
    console.log(`To run it, fill in engine/.env: ${missingKeys.join(", ")}`);
    console.log("To see it working with fake data instead: npm run demo\n");
  } else {
    console.log("\nREAL SETUP: engine/.env decides the mode (paper unless you changed it).\n");
    start("engine", PYTHON, ["-m", "engine"]);
  }
  start("web", process.execPath, next, { cwd: WEB });
  await waitFor(webPort, "The web app");
  console.log(`\n  Open      ${url}\n  Password  APP_PASSWORD in web/.env.local\n\n  Ctrl+C stops everything.\n`);
}
