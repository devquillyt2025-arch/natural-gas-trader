// A throwaway Postgres on localhost, for the demo and tests only: PGlite (real Postgres in WASM) built from
// the real migration and spoken to over the normal wire protocol, so the engine and the web app run exactly
// the code they run against Neon. Nothing is saved to disk. It does not check passwords.
import { readdir, readFile } from "node:fs/promises";
import net from "node:net";
import { PGlite } from "@electric-sql/pglite";

const SSL_REQUEST = 80877103;
const GSS_REQUEST = 80877104;
const CANCEL_REQUEST = 80877102;

/** The status byte of the last ReadyForQuery in a reply: "I" idle, "T" in a transaction, "E" failed one. */
function readyStatus(reply) {
  let status = null;
  for (let at = 0; at + 5 <= reply.length; ) {
    const length = 1 + new DataView(reply.buffer, reply.byteOffset + at + 1, 4).getInt32(0);
    if (reply[at] === 0x5a /* Z */) status = String.fromCharCode(reply[at + 5]);
    at += length;
  }
  return status;
}

export async function startDb({ port = 54321 } = {}) {
  const db = new PGlite();
  const dir = new URL("../../db/migrations/", import.meta.url);
  for (const file of (await readdir(dir)).sort()) await db.exec(await readFile(new URL(file, dir), "utf8"));
  await db.exec("set timezone = 'UTC'");

  // PGlite has ONE session. A client holds it for a whole request (every message up to Sync or Query), and
  // for a whole transaction, so another client's statement can never slip in between its parse and bind.
  let owner = null;
  const queue = [];
  const lock = (client) =>
    new Promise((resolve) => {
      if (owner === null || owner === client) {
        owner = client;
        resolve();
      } else queue.push([client, resolve]);
    });
  const release = () => {
    const next = queue.shift();
    owner = next ? next[0] : null;
    next?.[1]();
  };

  const sockets = new Set();
  const server = net.createServer((socket) => {
    sockets.add(socket);
    let buffer = Buffer.alloc(0);
    let started = false;
    let batch = [];
    let chain = Promise.resolve();
    let inTransaction = false;

    const submit = (messages) => {
      chain = chain
        .then(async () => {
          await lock(socket);
          try {
            await db.runExclusive(async () => {
              for (const message of messages) {
                const reply = await db.execProtocolRaw(new Uint8Array(message), { syncToFs: false });
                const status = readyStatus(reply);
                if (status) inTransaction = status !== "I";
                if (!socket.destroyed) socket.write(reply);
              }
            });
          } finally {
            if (!inTransaction) release();
          }
        })
        .catch((error) => {
          console.error("demo database:", error.message);
          socket.destroy();
        });
    };

    socket.on("data", (chunk) => {
      buffer = Buffer.concat([buffer, chunk]);
      for (;;) {
        if (!started) {
          // startup-phase messages have no type byte: length, then a code
          if (buffer.length < 8 || buffer.length < buffer.readInt32BE(0)) return;
          const message = buffer.subarray(0, buffer.readInt32BE(0));
          buffer = buffer.subarray(message.length);
          const code = message.readInt32BE(4);
          if (code === SSL_REQUEST || code === GSS_REQUEST) socket.write("N");
          else if (code === CANCEL_REQUEST) return void socket.end();
          else {
            started = true;
            submit([message]);
          }
        } else {
          if (buffer.length < 5 || buffer.length < 1 + buffer.readInt32BE(1)) return;
          const message = buffer.subarray(0, 1 + buffer.readInt32BE(1));
          buffer = buffer.subarray(message.length);
          const type = String.fromCharCode(message[0]);
          if (type === "X") return void socket.end(); // never forwarded: it would end the shared session
          batch.push(message);
          if (type === "Q" || type === "S" || type === "p" || type === "H") {
            submit(batch);
            batch = [];
          }
        }
      }
    });
    socket.on("error", () => {});
    socket.on("close", () => {
      sockets.delete(socket);
      chain = chain.then(async () => {
        if (owner !== socket) return;
        if (inTransaction) await db.exec("rollback");
        release();
      });
    });
  });

  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(port, "127.0.0.1", resolve);
  });

  return {
    db,
    port,
    url: `postgresql://postgres:demo@127.0.0.1:${port}/postgres?sslmode=disable`,
    async close() {
      for (const socket of sockets) socket.destroy();
      await new Promise((resolve) => server.close(resolve));
      await db.close();
    },
  };
}
