// npm run db:setup
// Reads the Neon connection string from engine/.env, creates the tables, gives the web app its own
// restricted login, and writes web/.env.local. Safe to run again. Never prints a connection string.
import { randomBytes } from "node:crypto";
import { copyFileSync, existsSync, readdirSync, readFileSync, writeFileSync } from "node:fs";
import { createRequire } from "node:module";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const ENGINE_ENV = process.env.ENGINE_ENV_FILE || path.join(ROOT, "engine", ".env");
const WEB_ENV = process.env.WEB_ENV_FILE || path.join(ROOT, "web", ".env.local");
const MIGRATIONS = path.join(ROOT, "db", "migrations");

function stop(message) {
  console.error(`\n✖ ${message}\n`);
  process.exit(1);
}

let pg;
try {
  pg = createRequire(path.join(ROOT, "web", "package.json"))("pg");
} catch {
  stop("The web packages are missing. Run: npm install --prefix web");
}

const readEnv = (text, name) => {
  const match = new RegExp(`^[ \\t]*${name}[ \\t]*=[ \\t]*(.*?)[ \\t]*\\r?$`, "m").exec(text);
  return match ? match[1].replace(/^(["'])(.*)\1$/, "$2") : "";
};
const writeEnv = (text, name, value) => {
  const line = `${name}=${value}`;
  const pattern = new RegExp(`^[ \\t]*${name}[ \\t]*=.*$`, "m");
  if (pattern.test(text)) return text.replace(pattern, () => line);
  return `${text.replace(/\n*$/, "\n")}${line}\n`;
};

if (!existsSync(ENGINE_ENV)) {
  copyFileSync(path.join(ROOT, "engine", ".env.example"), ENGINE_ENV);
  stop("I created engine/.env. Paste your Neon connection string after DATABASE_URL= in it, then run this again.");
}
const ownerUrl = readEnv(readFileSync(ENGINE_ENV, "utf8"), "DATABASE_URL");
if (!/^postgres(ql)?:\/\//.test(ownerUrl)) {
  stop("Paste your Neon connection string (it starts with postgresql://) after DATABASE_URL= in engine/.env.");
}

// node-postgres treats sslmode=require as verify-full and warns about it; say what we mean.
const forNode = (url) => url.replace(/sslmode=(require|prefer|verify-ca)/, "sslmode=verify-full");

async function connect(url) {
  const client = new pg.Client({ connectionString: forNode(url), connectionTimeoutMillis: 20_000 });
  await client.connect();
  return client;
}

let owner;
try {
  owner = await connect(ownerUrl);
} catch (error) {
  stop(`Could not connect with DATABASE_URL from engine/.env: ${error.message}`);
}

// Another app's `trades` or `events` table would be silently reused by "create table if not exists".
const OURS = {
  decisions: ["verdict_code", "snapshot", "mode"],
  trades: ["r_multiple", "exit_reason", "mode"],
  candles: ["tf", "ts", "v"],
  state: ["key", "value", "updated_at"],
  commands: ["payload", "status", "result"],
  events: ["level", "msg"],
  login_attempts: ["ip", "ts"],
};
const existing = await owner.query(
  `select table_name as name, json_agg(column_name) as columns from information_schema.columns
    where table_schema = 'public' group by table_name`,
);
const clashes = existing.rows.filter((t) => OURS[t.name] && !OURS[t.name].every((c) => t.columns.includes(c)));
if (clashes.length) {
  stop(
    `This database already holds another app's ${clashes.map((t) => t.name).join(", ")} table. ` +
      "Give NatGas Trader its own Neon project or database. Nothing was changed.",
  );
}

let webUrl;
try {
  for (const file of readdirSync(MIGRATIONS).sort()) {
    await owner.query(readFileSync(path.join(MIGRATIONS, file), "utf8"));
    console.log(`✔ applied db/migrations/${file}`);
  }

  let web = existsSync(WEB_ENV) ? readFileSync(WEB_ENV, "utf8") : readFileSync(path.join(ROOT, "web", ".env.example"), "utf8");

  // Keep an existing web login that still works, so a deployed copy of the web app is not cut off.
  const current = readEnv(web, "DATABASE_URL");
  if (current) {
    try {
      const probe = await connect(current);
      const { rows } = await probe.query("select current_user as name");
      await probe.end();
      if (rows[0].name === "natgas_web" && new URL(current).host === new URL(ownerUrl).host) webUrl = current;
    } catch {
      // stale: replaced below
    }
  }
  if (!webUrl) {
    // Neon wants length and a mix of character kinds; the suffix guarantees the mix.
    const password = `${randomBytes(24).toString("base64url")}-aZ9`;
    await owner.query(`alter role natgas_web with login password '${password}'`); // only [A-Za-z0-9_-]
    const url = new URL(ownerUrl);
    url.username = "natgas_web";
    url.password = password;
    webUrl = forNode(url.toString());
    web = writeEnv(web, "DATABASE_URL", webUrl);
    console.log("✔ created the web app's own database login (natgas_web)");
  } else {
    console.log("✔ the web app's database login already works");
  }

  if (readEnv(web, "APP_PASSWORD").length < 8) {
    web = writeEnv(web, "APP_PASSWORD", randomBytes(12).toString("base64url"));
    console.log("✔ chose a dashboard password: see APP_PASSWORD in web/.env.local (change it if you like)");
  }
  if (readEnv(web, "SESSION_SECRET").length < 32) {
    web = writeEnv(web, "SESSION_SECRET", randomBytes(32).toString("base64url"));
  }
  writeFileSync(WEB_ENV, web);
  console.log("✔ wrote web/.env.local");

  const probe = await connect(webUrl);
  await probe.query("select count(*) from public.state");
  await probe.end();
  console.log("✔ the web login can read the database\n\nDone. The engine still needs its Anthropic and Upstox keys in engine/.env.");
} catch (error) {
  stop(`Setup failed: ${error.message}`);
} finally {
  await owner.end().catch(() => {});
}
