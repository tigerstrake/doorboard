/**
 * A `node:sqlite`-backed {@link D1Like} for tests — real SQLite (so RETURNING, ON CONFLICT,
 * and the guarded CAS statements the store relies on execute for real), wrapped in D1's async
 * `prepare().bind().first()/.all()/.run()` shape. `wrangler`/miniflare cannot be installed in
 * the build sandbox, so this is how the D1 store's semantics are exercised; the same SQL runs
 * against the real `env.DB` binding in production.
 *
 * It also records **every value ever bound** to a statement, so the privacy tests can byte-scan
 * what the relay wrote (P-13, P-21) as well as what it still holds after a sweep.
 */
import { DatabaseSync } from "node:sqlite";

import { D1RelayStore } from "@/lib/d1/d1Store";
import type { D1Like, D1PreparedStatement, D1Result } from "@/lib/d1/types";

type Bindable = null | number | bigint | string | Uint8Array;

function coerce(value: unknown): Bindable {
  if (typeof value === "boolean") return value ? 1 : 0;
  if (value === undefined) return null;
  return value as Bindable;
}

class NodeStatement implements D1PreparedStatement {
  constructor(
    private readonly db: DatabaseSync,
    private readonly writeLog: string[],
    private readonly sql: string,
    private readonly args: unknown[] = [],
  ) {}

  bind(...values: unknown[]): D1PreparedStatement {
    return new NodeStatement(this.db, this.writeLog, this.sql, values);
  }

  private params(): Bindable[] {
    const params = this.args.map(coerce);
    this.writeLog.push(`${this.sql}\n${params.map(String).join("\n")}`);
    return params;
  }

  async first<T = Record<string, unknown>>(): Promise<T | null> {
    const row = this.db.prepare(this.sql).get(...this.params());
    return (row as T | undefined) ?? null;
  }

  async all<T = Record<string, unknown>>(): Promise<D1Result<T>> {
    const rows = this.db.prepare(this.sql).all(...this.params());
    return { results: rows as T[], meta: { changes: 0 } };
  }

  async run(): Promise<D1Result> {
    const info = this.db.prepare(this.sql).run(...this.params());
    return { results: [], meta: { changes: Number(info.changes) } };
  }
}

export class NodeSqliteD1 implements D1Like {
  private readonly db = new DatabaseSync(":memory:");
  /** Every statement and every bound value, in order — for the "never held" scans. */
  private readonly writeLog: string[] = [];

  prepare(query: string): D1PreparedStatement {
    return new NodeStatement(this.db, this.writeLog, query);
  }

  async exec(query: string): Promise<unknown> {
    this.db.exec(query);
    return null;
  }

  /**
   * Every row of every table, plus the schema, as one string: what the database holds
   * *right now*. This is the retention scan — after a TTL and a sweep, a sentinel must be
   * absent from here.
   */
  dumpRows(): string {
    const parts: string[] = [];
    const tables = this.db
      .prepare("SELECT name, sql FROM sqlite_master WHERE type = 'table' ORDER BY name")
      .all() as Array<{ name: string; sql: string }>;
    for (const table of tables) {
      parts.push(table.sql);
      for (const row of this.db.prepare(`SELECT * FROM "${table.name}"`).all()) {
        parts.push(`${table.name}:${JSON.stringify(row)}`);
      }
    }
    return parts.join("\n");
  }

  /**
   * Everything ever bound to a statement plus everything currently held — the write log the
   * old Redis double kept, so P-13/P-21 keep their meaning: if the relay ever touched a
   * plaintext sentinel or a raw token, it is in here.
   */
  dumpAll(): string {
    return [...this.writeLog, this.dumpRows()].join("\n");
  }
}

/** A fresh, fully migrated database — what a handler suite hands to `resolveStore`. */
export async function freshD1(): Promise<NodeSqliteD1> {
  const db = new NodeSqliteD1();
  await new D1RelayStore(db).migrate();
  return db;
}
