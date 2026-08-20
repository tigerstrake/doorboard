import React, { useCallback, useEffect, useRef, useState } from "react";
import { QRPlaceholder } from "@doorboard/ui-kit";

import {
  CreatedInvite,
  InviteSummary,
  RelayStatus,
  enrollmentApi,
} from "./enrollmentApi";

/**
 * Remote enrollment: mint an invite, show its QR on the doorboard, track and
 * revoke outstanding invites (ADR-0016).
 *
 * The QR is generated locally by the shared ui-kit component — never by a
 * third-party chart service, because the URL contains the invite secret and must
 * reach nobody but the person holding the phone.
 *
 * The URL is shown once and deliberately not persisted: the door keeps only
 * `sha256(secret)`, so there is nothing to re-display later.
 */

function relayBadge(relay: RelayStatus | null): { text: string; color: string } {
  if (!relay || !relay.configured) {
    return { text: "Relay not configured", color: "#888888" };
  }
  switch (relay.status) {
    case "ok":
      return { text: "Relay reachable", color: "#44ff44" };
    case "degraded":
      return { text: `Relay unreachable${relay.last_error ? ` (${relay.last_error})` : ""}`, color: "#ff9900" };
    default:
      return { text: "Relay poller stopped", color: "#ff9900" };
  }
}

export function AdminRemoteEnrollPanel({
  token,
  privacyEnabled,
  onEnrollmentLikely,
}: {
  token: string;
  privacyEnabled: boolean;
  /** Called when an invite is consumed, so the parent can refresh its people list. */
  onEnrollmentLikely: () => void;
}) {
  const [invites, setInvites] = useState<InviteSummary[]>([]);
  const [relay, setRelay] = useState<RelayStatus | null>(null);
  const [created, setCreated] = useState<CreatedInvite | null>(null);
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const openCountRef = useRef(0);

  const refresh = useCallback(async () => {
    if (!token) return;
    try {
      const [listed, status] = await Promise.all([
        enrollmentApi.listInvites(token, true),
        enrollmentApi.getRelayStatus(token),
      ]);
      // This panel runs on an unattended kiosk: an unexpected payload must
      // degrade to "no invites", never take the admin screen down.
      const safeList = Array.isArray(listed) ? listed : [];
      setInvites(safeList);
      setRelay(status);

      // An invite disappearing from "open" means a phone completed enrollment;
      // tell the parent so the enrolled-members list stops looking stale.
      const open = safeList.filter((invite) => invite.status === "open").length;
      if (open < openCountRef.current) onEnrollmentLikely();
      openCountRef.current = open;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load invite state.");
    }
  }, [token, onEnrollmentLikely]);

  useEffect(() => {
    void refresh();
    // Poll while the panel is open so a completed phone enrollment shows up
    // without the admin reloading the kiosk.
    const timer = window.setInterval(() => void refresh(), 5000);
    return () => window.clearInterval(timer);
  }, [refresh]);

  const handleCreate = async () => {
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const invite = await enrollmentApi.createInvite(token, label);
      setCreated(invite);
      setLabel("");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create an invite.");
    } finally {
      setBusy(false);
    }
  };

  const handleRevoke = async (inviteId: string) => {
    setError(null);
    try {
      await enrollmentApi.revokeInvite(token, inviteId);
      if (created?.invite_id === inviteId) setCreated(null);
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not revoke the invite.");
    }
  };

  const handleCopy = async () => {
    if (!created) return;
    try {
      await navigator.clipboard.writeText(created.url);
      setCopied(true);
    } catch {
      setError("Could not copy — select the link and copy it manually.");
    }
  };

  const badge = relayBadge(relay);
  const openInvites = invites.filter((invite) => invite.status === "open");
  const closedInvites = invites.filter((invite) => invite.status !== "open");

  return (
    <section style={{ marginTop: "28px", paddingTop: "20px", borderTop: "1px solid #333" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: "12px" }}>
        <h3 style={{ margin: 0 }}>Enroll from a phone</h3>
        <span style={{ display: "inline-flex", alignItems: "center", gap: "8px", fontSize: "0.85rem", opacity: 0.85 }}>
          <span
            style={{ width: "9px", height: "9px", borderRadius: "50%", backgroundColor: badge.color, display: "inline-block" }}
          />
          {badge.text}
        </span>
      </div>

      <p className="placeholder-subtext" style={{ marginTop: "6px" }}>
        Generate a single-use QR code. The phone encrypts its photos to this door before sending,
        so the relay website never sees a face or a name. Works off the home network.
      </p>

      {error && <p className="poll-error">{error}</p>}

      {relay && !relay.configured && (
        <div style={{ background: "#332211", borderLeft: "4px solid #ff9900", padding: "10px", margin: "10px 0", borderRadius: "4px" }}>
          <p style={{ margin: 0, fontSize: "0.9rem" }}>
            No relay is configured, so a phone cannot reach this door. Set{" "}
            <code>VISIOND_RELAY_BASE_URL</code> and <code>VISIOND_RELAY_DEVICE_TOKEN</code> on the
            door, then restart door-visiond. Enrolling at the doorboard still works.
          </p>
        </div>
      )}

      <div style={{ display: "flex", gap: "10px", alignItems: "center", margin: "14px 0" }}>
        <input
          type="text"
          value={label}
          maxLength={64}
          placeholder="Whose phone? (optional note)"
          aria-label="Invite label"
          onChange={(e) => setLabel(e.target.value)}
          style={{ flex: 1 }}
        />
        <button className="phrase-btn" onClick={handleCreate} disabled={busy || privacyEnabled}>
          {busy ? "Creating…" : "Create QR invite"}
        </button>
      </div>

      {privacyEnabled && (
        <p className="placeholder-subtext">
          Privacy mode is on, so new invites are blocked along with all enrollment.
        </p>
      )}

      {created && (
        <div style={{ background: "#111", border: "1px solid #333", borderRadius: "8px", padding: "18px", marginBottom: "18px", textAlign: "center" }}>
          <h4 style={{ margin: "0 0 4px 0" }}>Scan this with the phone</h4>
          <p style={{ fontSize: "0.85rem", opacity: 0.7, margin: "0 0 14px 0" }}>
            Valid until {new Date(created.expires_at).toLocaleString()} · single use ·{" "}
            {created.max_images} photos max
          </p>

          <QRPlaceholder
            url={created.url}
            alt="Enrollment invitation QR code"
            text="Open the camera app and scan"
            size={260}
          />

          <div style={{ display: "flex", justifyContent: "center", gap: "10px", marginTop: "10px" }}>
            <button className="phrase-btn" onClick={handleCopy}>
              {copied ? "Copied" : "Copy link"}
            </button>
            <button className="phrase-btn" onClick={() => setCreated(null)}>
              Hide
            </button>
          </div>
          <p style={{ fontSize: "0.75rem", opacity: 0.55, marginTop: "12px", marginBottom: 0 }}>
            This link is shown only once. Hiding it does not revoke it — use Revoke below for that.
          </p>
        </div>
      )}

      <h4 style={{ marginBottom: "8px" }}>Open invites ({openInvites.length})</h4>
      {openInvites.length === 0 ? (
        <p className="placeholder-subtext">No invites waiting to be used.</p>
      ) : (
        <div className="admin-recording-list">
          {openInvites.map((invite) => (
            <div
              key={invite.invite_id}
              className="admin-recording-row"
              style={{ display: "flex", justifyContent: "space-between", alignItems: "center", padding: "10px" }}
            >
              <div>
                <strong>{invite.label || "Unlabelled invite"}</strong>
                <div style={{ fontSize: "0.8rem", opacity: 0.6 }}>
                  expires {new Date(invite.expires_at).toLocaleTimeString()}
                </div>
              </div>
              <button className="delete-recording-btn" onClick={() => handleRevoke(invite.invite_id)}>
                Revoke
              </button>
            </div>
          ))}
        </div>
      )}

      {closedInvites.length > 0 && (
        <details style={{ marginTop: "14px" }}>
          <summary style={{ cursor: "pointer", opacity: 0.75 }}>
            Past invites ({closedInvites.length})
          </summary>
          <div className="admin-recording-list" style={{ marginTop: "8px" }}>
            {closedInvites.map((invite) => (
              <div
                key={invite.invite_id}
                className="admin-recording-row"
                style={{ display: "flex", justifyContent: "space-between", padding: "8px 10px", fontSize: "0.85rem" }}
              >
                <span>{invite.label || "Unlabelled invite"}</span>
                <span className="route-tag">{invite.status}</span>
              </div>
            ))}
          </div>
        </details>
      )}
    </section>
  );
}
