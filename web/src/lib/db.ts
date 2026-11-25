// SERVER ONLY. DATABASE_URL logs in as the restricted `natgas_web` role: it can read what the dashboard
// shows and queue commands, and nothing else (see db/migrations). The browser never talks to the database.
import { Pool } from "pg";

const shared = globalThis as unknown as { natgasPool?: Pool };

function pool(): Pool {
  if (!shared.natgasPool) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) throw new Error("DATABASE_URL is not set (see web/.env.example)");
    const created = new Pool({ connectionString, max: 4, idleTimeoutMillis: 10_000, connectionTimeoutMillis: 10_000, query_timeout: 15_000 });
    created.on("error", (e) => console.error("idle database connection failed:", e.message));
    shared.natgasPool = created;
  }
  return shared.natgasPool;
}

/** A query returning one row with one JSON column `j`, so values arrive as plain strings and numbers. */
export async function queryJson<T>(sql: string, params: unknown[] = []): Promise<T> {
  const result = await pool().query<{ j: T }>(sql, params);
  return result.rows[0]!.j;
}

/** Any SELECT written in this codebase (never user input) as an array of rows. */
export const queryRows = <T>(select: string, params: unknown[] = []): Promise<T[]> =>
  queryJson<T[]>(`select coalesce(json_agg(q), '[]'::json) as j from (${select}) q`, params);

export async function execute(sql: string, params: unknown[] = []): Promise<void> {
  await pool().query(sql, params);
}
