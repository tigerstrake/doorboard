import React, { useCallback, useEffect, useState } from "react";

import { Visit, VisitCount, enrollmentApi } from "./enrollmentApi";

/**
 * Arrival history for the admin panel (ADR-0018 §1).
 *
 * The log had endpoints but no interface, which meant the only way to see or purge
 * your own arrival history was `curl` — a poor state for data the consent statement
 * promises is visible to the household admin and deletable on request.
 *
 * Admin-only, deliberately: this is presence data, and ADR-0005 §5 keeps visitor
 * logs off public routes (E-24). There is no public counterpart and there must not
 * be one.
 *
 * Retention is unbounded by the owner's decision, so the delete controls are the
 * counterweight and are given equal prominence rather than hidden behind a menu.
 */

function formatWhen(iso: string): string {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return iso;
  return when.toLocaleString();
}

/** "18:32 → 18:47 (15 min)", or just the arrival if they were seen once. */
function formatStay(visit: Visit): string {
  const arrived = new Date(visit.arrived_at);
  const last = new Date(visit.last_seen_at);
  if (Number.isNaN(arrived.getTime()) || Number.isNaN(last.getTime())) return "";
  const minutes = Math.round((last.getTime() - arrived.getTime()) / 60000);
  if (minutes < 1) return "briefly";
  return `${minutes} min`;
}

export function AdminVisitLogPanel({ token }: { token: string }) {
  const [visits, setVisits] = useState<Visit[]>([]);
  const [counts, setCounts] = useState<VisitCount[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [confirmPurge, setConfirmPurge] = useState<"all" | string | null>(null);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const [listed, tallies] = await Promise.all([
        enrollmentApi.getVisits(token),
        enrollmentApi.getVisitCounts(token),
      ]);
      // This is an unattended kiosk: an unexpected payload must degrade to empty,
      // never take the admin screen down.
      setVisits(Array.isArray(listed) ? listed : []);
      setCounts(Array.isArray(tallies) ? tallies : []);
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load the arrival log.");
    } finally {
      setLoaded(true);
    }
  }, [token]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const purge = async (scope: "all" | string) => {
    setError(null);
    try {
      await enrollmentApi.purgeVisits(token, scope === "all" ? undefined : scope);
      setConfirmPurge(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the arrival log.");
    }
  };

  const purgeTarget =
    confirmPurge && confirmPurge !== "all"
      ? counts.find((c) => c.person_id === confirmPurge)
      : undefined;

  return (
    <section style={{ marginTop: "28px", paddingTop: "20px", borderTop: "1px solid #333" }}>
      <h3 style={{ margin: 0 }}>Arrival log</h3>
      <p className="placeholder-subtext" style={{ marginTop: "6px" }}>
        When the door recognised someone. Kept until deleted, admin-only, and never shown on the
        wallboard. Unenrolling a person also erases their arrivals.
      </p>

      {error && <p className="poll-error">{error}</p>}

      {counts.length > 0 && (
        <>
          <h4 style={{ marginBottom: "8px" }}>Per person</h4>
          <div className="admin-recording-list">
            {counts.map((count) => (
              <div
                key={count.person_id}
                className="admin-recording-row"
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  padding: "10px",
                }}
              >
                <div>
                  <strong>{count.display_name}</strong>
                  <div style={{ fontSize: "0.8rem", opacity: 0.6 }}>
                    {count.visits} {count.visits === 1 ? "arrival" : "arrivals"} · last{" "}
                    {formatWhen(count.last_seen_at)}
                  </div>
                </div>
                <button
                  className="delete-recording-btn"
                  onClick={() => setConfirmPurge(count.person_id)}
                >
                  Forget arrivals
                </button>
              </div>
            ))}
          </div>
        </>
      )}

      <h4 style={{ marginBottom: "8px", marginTop: "18px" }}>
        Recent arrivals ({visits.length})
      </h4>
      {!loaded ? (
        <p className="placeholder-subtext">Loading…</p>
      ) : visits.length === 0 ? (
        <p className="placeholder-subtext">
          No arrivals recorded yet. Only people enrolled under the current consent statement are
          logged — anyone who enrolled earlier agreed to a greeting, not to being logged, so they
          stay absent until they re-enrol.
        </p>
      ) : (
        <div className="admin-recording-list">
          {visits.map((visit) => (
            <div
              key={visit.visit_id}
              className="admin-recording-row"
              style={{ display: "flex", justifyContent: "space-between", padding: "8px 10px" }}
            >
              <span>
                <strong>{visit.display_name}</strong>{" "}
                <span style={{ opacity: 0.6 }}>{formatWhen(visit.arrived_at)}</span>
              </span>
              <span className="route-tag">{formatStay(visit)}</span>
            </div>
          ))}
        </div>
      )}

      {visits.length > 0 && (
        <div style={{ marginTop: "14px" }}>
          <button className="delete-recording-btn" onClick={() => setConfirmPurge("all")}>
            Delete the entire arrival log
          </button>
        </div>
      )}

      {confirmPurge && (
        <div
          className="modal-overlay"
          style={{
            position: "fixed",
            inset: 0,
            backgroundColor: "rgba(0,0,0,0.8)",
            display: "flex",
            justifyContent: "center",
            alignItems: "center",
            zIndex: 1000,
          }}
        >
          <div
            style={{
              background: "#222",
              padding: "24px",
              borderRadius: "8px",
              maxWidth: "450px",
              width: "90%",
              border: "1px solid #444",
            }}
          >
            <h4 style={{ margin: "0 0 16px 0", color: "#ff4444" }}>Delete arrival history</h4>
            <p>
              {confirmPurge === "all"
                ? `Delete all ${visits.length} recorded arrivals?`
                : `Delete every recorded arrival for ${purgeTarget?.display_name ?? "this person"}?`}
            </p>
            <p style={{ color: "#ff8888", fontSize: "0.9rem" }}>
              ⚠️ Immediate and irreversible. Face templates are <strong>not</strong> affected —
              they stay enrolled and will still be recognised and greeted. This only forgets where
              they have been.
            </p>
            <div
              style={{ display: "flex", justifyContent: "flex-end", gap: "12px", marginTop: "24px" }}
            >
              <button className="phrase-btn" onClick={() => setConfirmPurge(null)}>
                Cancel
              </button>
              <button
                className="delete-recording-btn"
                style={{ float: "none" }}
                onClick={() => void purge(confirmPurge)}
              >
                Delete permanently
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
