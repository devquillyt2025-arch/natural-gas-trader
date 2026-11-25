// Runs the migration on a real Postgres engine (PGlite), then attacks it as the web app's role.
import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { before, test } from "node:test";
import { PGlite } from "@electric-sql/pglite";

const READABLE = ["decisions", "trades", "candles", "state", "commands", "events", "decision_days"];

let db;
let migration = "";

before(async () => {
  db = new PGlite();
  const dir = new URL("../migrations/", import.meta.url);
  for (const file of (await readdir(dir)).sort()) migration += await readFile(new URL(file, dir), "utf8");
  await db.exec(migration);
  await db.exec(`
    insert into public.decisions (id, ts, action, approved, verdict_code, mode)
      values (1, now(), 'HOLD', true, 'hold', 'paper');
    insert into public.trades values ('t1', now(), now(), 'LONG', 1, 250, 300, 301, 190, 0.4, 'target', '', 1, 'paper');
    insert into public.candles (tf, ts, o, h, l, c) values ('15m', now(), 1, 2, 0, 1);
    insert into public.state (key, value) values ('ltp', '{"price": 300}');
    insert into public.events values (1, now(), 'info', 'hello');
    insert into public.commands (type) values ('kill');
  `);
});

async function asWeb(fn) {
  await db.exec("set role natgas_web");
  try {
    await fn();
  } finally {
    await db.exec("reset role");
  }
}
const count = async (table) => (await db.query(`select count(*)::int n from public.${table}`)).rows[0].n;
const denied = (sql) => assert.rejects(db.exec(sql), /permission denied|must be owner|violates/i, sql);

test("running the migration again is harmless and keeps the data", async () => {
  await db.exec(migration);
  assert.equal(await count("trades"), 1);
});

test("candles may be 5m, 15m or daily, and nothing else", async () => {
  await db.exec("insert into public.candles (tf, ts, o, h, l, c) values ('5m', now(), 1, 2, 0, 1)");
  await denied("insert into public.candles (tf, ts, o, h, l, c) values ('1m', now(), 1, 2, 0, 1)");
  await db.exec("delete from public.candles where tf = '5m'");
});

test("the web role reads everything the dashboard shows", async () => {
  await asWeb(async () => {
    for (const table of READABLE) assert.ok((await count(table)) >= 1, table);
  });
});

test("the web role may only queue a pending command", async () => {
  await asWeb(async () => {
    await db.exec(`insert into public.commands (type, payload) values ('update_limits', '{"max_lots": 1}')`);
    const row = (await db.query("select status, result from public.commands order by id desc limit 1")).rows[0];
    assert.deepEqual(row, { status: "pending", result: null });
    await denied("insert into public.commands (type, status) values ('kill', 'acked')");
    await denied("insert into public.commands (type, ts) values ('resume', '2030-01-01')");
    await denied("insert into public.commands (id, type) values (999, 'resume')");
    await denied("insert into public.commands (type) values ('go_live')");
  });
});

test("the web role cannot change or delete engine data, or the schema", async () => {
  await asWeb(async () => {
    await denied("update public.commands set status = 'acked'");
    await denied("delete from public.commands");
    await denied("update public.state set value = '{}'");
    await denied("insert into public.state (key) values ('position')");
    await denied("delete from public.trades");
    await denied("update public.decisions set approved = false");
    await denied("insert into public.events values (9, now(), 'info', 'forged')");
    await denied("delete from public.candles");
    await denied("truncate public.trades");
    await denied("drop table public.trades");
    await denied("create table public.evil (x int)");
  });
});

test("the web role can count and clear sign-in attempts", async () => {
  await asWeb(async () => {
    await db.exec("insert into public.login_attempts (ip) values ('1.2.3.4')");
    assert.equal(await count("login_attempts"), 1);
    await db.exec("delete from public.login_attempts where ip = '1.2.3.4'");
    await denied("update public.login_attempts set ts = now()");
  });
});

test("the engine (database owner) can do its job", async () => {
  await db.exec(`
    insert into public.state (key, value) values ('ltp', '{"price": 301}')
      on conflict (key) do update set value = excluded.value;
    update public.commands set status = 'acked', result = 'ok', payload = '{}' where status = 'pending';
    delete from public.candles where tf = '15m';
  `);
  assert.equal(await count("candles"), 0);
});
