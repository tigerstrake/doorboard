/**
 * The relay store on Cloudflare D1 (ADR-0043 §1) — a drop-in for the Redis store's method
 * surface, with the same retention windows and the lease/terminal/consumed/rate-limit
 * semantics made *atomic* by single guarded SQL statements (the Redis version relied on
 * per-key serialization plus a single-threaded poller). Everything here is either ciphertext
 * or an opaque identifier; no face image, display name, embedding, or `person_id` is stored
 * (E-9), and the visitor snapshot carries a boolean `attributed`, never a name (ADR-0044).
 *
 * `now` is injectable so tests are deterministic; production leaves it as `Date.now`.
 */
import type {
  BundleState,
  DoorKeyRecord,
  RelayStore,
  StoredBundle,
  StoredInvite,
  StoredStatus,
  StoredVisitorSnapshot,
} from "../relayTypes";
import {
  BUNDLE_TTL_S,
  INVITE_GRACE_S,
  MAX_PICKUP_BATCH,
  MAX_VISITOR_BATCH,
  PICKUP_LEASE_S,
  STATUS_TTL_S,
  VISITOR_ACTION_TTL_S,
  VISITOR_LEASE_S,
  VISITOR_SNAPSHOT_GRACE_S,
} from "../relayTypes";
import type { SealedBundle } from "../contracts";
import { SCHEMA_SQL } from "./schema";
import type { D1Like } from "./types";

const TERMINAL_SQL = "('enrolled','expired')";

export class D1RelayStore implements RelayStore {
  constructor(
    private readonly db: D1Like,
    private readonly now: () => number = () => Date.now(),
  ) {}

  /** A constructed D1 store always has its binding — the route's 503 gate is for the Redis path. */
  configured(): boolean {
    return true;
  }

  /** Create the schema. Idempotent (every statement is `IF NOT EXISTS`). */
  async migrate(): Promise<void> {
    await this.db.exec(SCHEMA_SQL);
  }

  // -- door key -------------------------------------------------------------

  async putDoorKey(record: DoorKeyRecord): Promise<void> {
    await this.db
      .prepare(
        "INSERT INTO door_key (id, record_json) VALUES ('active', ?) " +
          "ON CONFLICT(id) DO UPDATE SET record_json = excluded.record_json",
      )
      .bind(JSON.stringify(record))
      .run();
  }

  async getDoorKey(): Promise<DoorKeyRecord | null> {
    const row = await this.db
      .prepare("SELECT record_json FROM door_key WHERE id = 'active'")
      .first<{ record_json: string }>();
    return row ? (JSON.parse(row.record_json) as DoorKeyRecord) : null;
  }

  // -- invites --------------------------------------------------------------

  async putInvite(inviteId: string, invite: Omit<StoredInvite, "consumed">): Promise<void> {
    // Re-registration (the Pi resyncs after an outage) must not resurrect a consumed invite:
    // ON CONFLICT never touches `consumed`, so a row we already saw consumed stays consumed.
    const expiresMs = Date.parse(invite.expires_at) + INVITE_GRACE_S * 1000;
    await this.db
      .prepare(
        "INSERT INTO invite (invite_id, secret_sha256, expires_at, max_images, consumed, expires_ms) " +
          "VALUES (?, ?, ?, ?, 0, ?) " +
          "ON CONFLICT(invite_id) DO UPDATE SET " +
          "secret_sha256 = excluded.secret_sha256, expires_at = excluded.expires_at, " +
          "max_images = excluded.max_images, expires_ms = excluded.expires_ms",
      )
      .bind(inviteId, invite.secret_sha256, invite.expires_at, invite.max_images, expiresMs)
      .run();
  }

  async getInvite(inviteId: string): Promise<StoredInvite | null> {
    const row = await this.db
      .prepare(
        "SELECT secret_sha256, expires_at, max_images, consumed FROM invite " +
          "WHERE invite_id = ? AND expires_ms > ?",
      )
      .bind(inviteId, this.now())
      .first<{ secret_sha256: string; expires_at: string; max_images: number; consumed: number }>();
    if (!row) return null;
    return {
      secret_sha256: row.secret_sha256,
      expires_at: row.expires_at,
      max_images: row.max_images,
      consumed: row.consumed === 1,
    };
  }

  async markInviteConsumed(inviteId: string): Promise<void> {
    await this.db
      .prepare("UPDATE invite SET consumed = 1 WHERE invite_id = ?")
      .bind(inviteId)
      .run();
  }

  async deleteInvite(inviteId: string): Promise<void> {
    await this.db.prepare("DELETE FROM invite WHERE invite_id = ?").bind(inviteId).run();
  }

  // -- bundles --------------------------------------------------------------

  async putBundle(bundle: SealedBundle): Promise<string> {
    const now = this.now();
    const submittedAt = new Date(now).toISOString();
    await this.db
      .prepare(
        "INSERT INTO bundle (bundle_id, invite_id, bundle_json, submitted_at, created_ms, " +
          "bundle_expires_ms, lease_until_ms, in_pending, status, status_reason, " +
          "status_updated_at, status_expires_ms) " +
          "VALUES (?, ?, ?, ?, ?, ?, 0, 1, 'pending', NULL, ?, ?) " +
          "ON CONFLICT(bundle_id) DO UPDATE SET " +
          "invite_id = excluded.invite_id, bundle_json = excluded.bundle_json, " +
          "submitted_at = excluded.submitted_at, created_ms = excluded.created_ms, " +
          "bundle_expires_ms = excluded.bundle_expires_ms, lease_until_ms = 0, in_pending = 1, " +
          "status = 'pending', status_reason = NULL, status_updated_at = excluded.status_updated_at, " +
          "status_expires_ms = excluded.status_expires_ms",
      )
      .bind(
        bundle.bundle_id,
        bundle.invite_id,
        JSON.stringify(bundle),
        submittedAt,
        now,
        now + BUNDLE_TTL_S * 1000,
        submittedAt,
        now + STATUS_TTL_S * 1000,
      )
      .run();
    return submittedAt;
  }

  /**
   * Claim up to {@link MAX_PICKUP_BATCH} collectable bundles and push their lease out, so a
   * concurrent poll cannot take the same work. Atomic: the claim is one guarded UPDATE, so
   * two pollers cannot both win a bundle (the Redis version's read-then-rescore could).
   */
  async leaseBundles(): Promise<StoredBundle[]> {
    const now = this.now();
    // 1. Expire bundles whose ciphertext TTL lapsed before collection — status survives.
    await this.db
      .prepare(
        "UPDATE bundle SET in_pending = 0, bundle_json = NULL, status = 'expired', " +
          "status_reason = 'bundle_expired', status_updated_at = ?, status_expires_ms = ? " +
          `WHERE in_pending = 1 AND bundle_expires_ms <= ? AND status NOT IN ${TERMINAL_SQL}`,
      )
      .bind(new Date(now).toISOString(), now + STATUS_TTL_S * 1000, now)
      .run();
    // 2. Atomically claim the oldest collectable bundles and lease them.
    const claimed = await this.db
      .prepare(
        "UPDATE bundle SET lease_until_ms = ?, status = 'collected', status_updated_at = ?, " +
          "status_expires_ms = ? " +
          "WHERE bundle_id IN (SELECT bundle_id FROM bundle " +
          "WHERE in_pending = 1 AND lease_until_ms <= ? AND bundle_expires_ms > ? " +
          "ORDER BY created_ms LIMIT ?) " +
          `AND status NOT IN ${TERMINAL_SQL} ` +
          "RETURNING bundle_json, submitted_at",
      )
      .bind(
        now + PICKUP_LEASE_S * 1000,
        new Date(now).toISOString(),
        now + STATUS_TTL_S * 1000,
        now,
        now,
        MAX_PICKUP_BATCH,
      )
      .all<{ bundle_json: string; submitted_at: string }>();
    return claimed.results.map((r) => ({
      bundle: JSON.parse(r.bundle_json) as SealedBundle,
      submitted_at: r.submitted_at,
    }));
  }

  /**
   * Delete the ciphertext and record the outcome (ADR-0016 §5). Returns the bundle's
   * `invite_id` when the ciphertext was still present, so the caller can close the invite out.
   */
  async finishBundle(
    bundleId: string,
    status: BundleState,
    reason: string | null,
  ): Promise<string | null> {
    const existing = await this.db
      .prepare("SELECT invite_id, bundle_json FROM bundle WHERE bundle_id = ?")
      .bind(bundleId)
      .first<{ invite_id: string; bundle_json: string | null }>();
    const inviteId = existing && existing.bundle_json != null ? existing.invite_id : null;
    // Drop the ciphertext and remove from the pending set regardless of the status guard.
    await this.db
      .prepare("UPDATE bundle SET bundle_json = NULL, in_pending = 0 WHERE bundle_id = ?")
      .bind(bundleId)
      .run();
    await this.setStatus(bundleId, status, reason);
    return inviteId;
  }

  /**
   * Set the status, never regressing a terminal one (a late duplicate ack cannot un-enroll).
   * An UPSERT so a status can be recorded even for a bundle row already swept.
   */
  async setStatus(bundleId: string, status: BundleState, reason: string | null): Promise<void> {
    const now = this.now();
    await this.db
      .prepare(
        "INSERT INTO bundle (bundle_id, invite_id, bundle_json, submitted_at, created_ms, " +
          "bundle_expires_ms, lease_until_ms, in_pending, status, status_reason, " +
          "status_updated_at, status_expires_ms) " +
          "VALUES (?, '', NULL, '', 0, 0, 0, 0, ?, ?, ?, ?) " +
          "ON CONFLICT(bundle_id) DO UPDATE SET " +
          `status = CASE WHEN bundle.status IN ${TERMINAL_SQL} AND bundle.status <> excluded.status ` +
          "THEN bundle.status ELSE excluded.status END, " +
          `status_reason = CASE WHEN bundle.status IN ${TERMINAL_SQL} AND bundle.status <> excluded.status ` +
          "THEN bundle.status_reason ELSE excluded.status_reason END, " +
          `status_updated_at = CASE WHEN bundle.status IN ${TERMINAL_SQL} AND bundle.status <> excluded.status ` +
          "THEN bundle.status_updated_at ELSE excluded.status_updated_at END, " +
          `status_expires_ms = CASE WHEN bundle.status IN ${TERMINAL_SQL} AND bundle.status <> excluded.status ` +
          "THEN bundle.status_expires_ms ELSE excluded.status_expires_ms END",
      )
      .bind(bundleId, status, reason, new Date(now).toISOString(), now + STATUS_TTL_S * 1000)
      .run();
  }

  async getStatus(bundleId: string): Promise<StoredStatus | null> {
    const row = await this.db
      .prepare(
        "SELECT status, status_reason, status_updated_at FROM bundle " +
          "WHERE bundle_id = ? AND status_expires_ms > ?",
      )
      .bind(bundleId, this.now())
      .first<{ status: string; status_reason: string | null; status_updated_at: string }>();
    if (!row) return null;
    return {
      status: row.status as BundleState,
      reason: row.status_reason,
      updated_at: row.status_updated_at,
    };
  }

  async pendingCount(): Promise<number> {
    const row = await this.db
      .prepare("SELECT COUNT(*) AS c FROM bundle WHERE in_pending = 1")
      .first<{ c: number }>();
    return row?.c ?? 0;
  }

  // -- visitor surface (ADR-0017) ------------------------------------------

  async putVisitorSnapshot(snapshot: StoredVisitorSnapshot): Promise<void> {
    const expiresMs = Date.parse(snapshot.expires_at) + VISITOR_SNAPSHOT_GRACE_S * 1000;
    await this.db
      .prepare(
        "INSERT INTO visitor_snapshot (session_id, token_sha256, snapshot_json, expires_ms) " +
          "VALUES (?, ?, ?, ?) " +
          "ON CONFLICT(session_id) DO UPDATE SET token_sha256 = excluded.token_sha256, " +
          "snapshot_json = excluded.snapshot_json, expires_ms = excluded.expires_ms",
      )
      .bind(snapshot.session_id, snapshot.session_token_sha256, JSON.stringify(snapshot), expiresMs)
      .run();
  }

  async getVisitorSnapshotByTokenHash(tokenHash: string): Promise<StoredVisitorSnapshot | null> {
    const row = await this.db
      .prepare(
        "SELECT snapshot_json FROM visitor_snapshot WHERE token_sha256 = ? AND expires_ms > ? " +
          "ORDER BY expires_ms DESC LIMIT 1",
      )
      .bind(tokenHash, this.now())
      .first<{ snapshot_json: string }>();
    if (!row) return null;
    const snapshot = JSON.parse(row.snapshot_json) as StoredVisitorSnapshot;
    // Defence in depth: the hash must still match the snapshot found (a stale row cannot hand
    // one visitor another's session).
    return snapshot.session_token_sha256 === tokenHash ? snapshot : null;
  }

  async queueVisitorAction(actionId: string, action: unknown): Promise<void> {
    const now = this.now();
    await this.db
      .prepare(
        "INSERT INTO visitor_action (action_id, session_id, action_json, created_ms, " +
          "lease_until_ms, expires_ms) VALUES (?, ?, ?, ?, 0, ?) " +
          "ON CONFLICT(action_id) DO NOTHING",
      )
      .bind(
        actionId,
        (action as { session_id?: string }).session_id ?? "",
        JSON.stringify(action),
        now,
        now + VISITOR_ACTION_TTL_S * 1000,
      )
      .run();
  }

  /** Claim pending visitor actions, leasing them so a concurrent poll skips them. */
  async leaseVisitorActions(): Promise<unknown[]> {
    const now = this.now();
    const claimed = await this.db
      .prepare(
        "UPDATE visitor_action SET lease_until_ms = ? " +
          "WHERE action_id IN (SELECT action_id FROM visitor_action " +
          "WHERE lease_until_ms <= ? AND expires_ms > ? ORDER BY created_ms LIMIT ?) " +
          "RETURNING action_json",
      )
      .bind(now + VISITOR_LEASE_S * 1000, now, now, MAX_VISITOR_BATCH)
      .all<{ action_json: string }>();
    return claimed.results.map((r) => JSON.parse(r.action_json) as unknown);
  }

  /** Retire a collected action and fold its outcome into the visitor's snapshot. */
  async completeVisitorAction(
    sessionId: string,
    actionId: string,
    outcome: unknown,
  ): Promise<void> {
    await this.db
      .prepare("DELETE FROM visitor_action WHERE action_id = ?")
      .bind(actionId)
      .run();
    const row = await this.db
      .prepare("SELECT snapshot_json, expires_ms FROM visitor_snapshot WHERE session_id = ?")
      .bind(sessionId)
      .first<{ snapshot_json: string; expires_ms: number }>();
    if (!row) return;
    const snapshot = JSON.parse(row.snapshot_json) as StoredVisitorSnapshot;
    const outcomes = [
      ...snapshot.outcomes.filter(
        (existing) => (existing as { action_id?: string }).action_id !== actionId,
      ),
      outcome,
    ].slice(-16);
    await this.db
      .prepare("UPDATE visitor_snapshot SET snapshot_json = ? WHERE session_id = ?")
      .bind(JSON.stringify({ ...snapshot, outcomes }), sessionId)
      .run();
  }

  async pendingVisitorActionCount(): Promise<number> {
    const now = this.now();
    const row = await this.db
      .prepare("SELECT COUNT(*) AS c FROM visitor_action WHERE expires_ms > ?")
      .bind(now)
      .first<{ c: number }>();
    return row?.c ?? 0;
  }

  // -- rate limiting --------------------------------------------------------

  /** Fixed-window counter. Atomic upsert-increment with window reset; true = within budget. */
  async underRateLimit(
    scope: string,
    subject: string,
    limit: number,
    windowS: number,
  ): Promise<boolean> {
    const now = this.now();
    const row = await this.db
      .prepare(
        "INSERT INTO rate_limit (key, count, window_expires_ms) VALUES (?, 1, ?) " +
          "ON CONFLICT(key) DO UPDATE SET " +
          "count = CASE WHEN rate_limit.window_expires_ms <= ? THEN 1 ELSE rate_limit.count + 1 END, " +
          "window_expires_ms = CASE WHEN rate_limit.window_expires_ms <= ? THEN ? " +
          "ELSE rate_limit.window_expires_ms END " +
          "RETURNING count",
      )
      .bind(`${scope}:${subject}`, now + windowS * 1000, now, now, now + windowS * 1000)
      .first<{ count: number }>();
    return (row?.count ?? 1) <= limit;
  }

  // -- maintenance ----------------------------------------------------------

  /** Delete rows past their retention window. Called by the scheduled sweep (cron). */
  async sweepExpired(): Promise<void> {
    const now = this.now();
    await this.db.prepare("DELETE FROM invite WHERE expires_ms <= ?").bind(now).run();
    await this.db
      .prepare(`DELETE FROM bundle WHERE status_expires_ms <= ? AND bundle_json IS NULL`)
      .bind(now)
      .run();
    await this.db.prepare("DELETE FROM rate_limit WHERE window_expires_ms <= ?").bind(now).run();
    await this.db.prepare("DELETE FROM visitor_snapshot WHERE expires_ms <= ?").bind(now).run();
    await this.db.prepare("DELETE FROM visitor_action WHERE expires_ms <= ?").bind(now).run();
  }
}
