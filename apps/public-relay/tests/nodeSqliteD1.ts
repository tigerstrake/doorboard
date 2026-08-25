/**
 * A `node:sqlite`-backed {@link D1Like} for tests — real SQLite (so RETURNING, ON CONFLICT,
 * and the guarded CAS statements the store relies on execute for real), wrapped in D1's async
 * `prepare().bind().first()/.all()/.run()` shape. `wrangler`/miniflare cannot be installed in
 * the build sandbox, so this is how the D1 store's semantics are exercised; the same SQL runs
 * against the real `env.DB` binding in production.
 */
import { DatabaseSync } from "node:sqlite";

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
    private readonly sql: string,
    private readonly args: unknown[] = [],
  ) {}

  bind(...values: unknown[]): D1PreparedStatement {
    return new NodeStatement(this.db, this.sql, values);
  }

  private params(): Bindable[] {
    return this.args.map(coerce);
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

  prepare(query: string): D1PreparedStatement {
    return new NodeStatement(this.db, query);
  }

  async exec(query: string): Promise<unknown> {
    this.db.exec(query);
    return null;
  }
}
