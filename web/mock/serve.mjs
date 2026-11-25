// node mock/serve.mjs [--seed] [--port 54321]
// --seed fills the database with a fixed snapshot and keeps its heartbeat alive (UI work and the smoke test).
// Without it the database is empty, ready for a real engine: python -m engine.simulate --serve ...
import { startDb } from "./db-server.mjs";
import { beat, seed } from "./seed.mjs";

const at = process.argv.indexOf("--port");
const server = await startDb({ port: at === -1 ? 54321 : Number(process.argv[at + 1]) });
if (process.argv.includes("--seed")) {
  await seed(server.db);
  setInterval(() => void beat(server.db).catch(() => {}), 5000);
}
console.log(`demo database on ${server.url}`);
