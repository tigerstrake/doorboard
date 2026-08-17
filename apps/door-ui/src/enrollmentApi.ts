import { ApiError } from "./socialApi";

import { API_BASE } from "./apiBase";

// Routed through door-api rather than at door-visiond and door-media directly (ADR-0024).
//
// Those two bind 127.0.0.1 on purpose — door-visiond's is the biometric enrollment API —
// so calling them from the browser only ever worked when the browser was the Pi's own.
// From a laptop every panel here failed, and failed *silently*: the enrollment list drew
// "Enrolled Members (0)" for a door with two people enrolled, and the relay panel said
// "Relay not configured" for a configured relay. Wrong facts, not errors.
//
// door-api is already LAN-exposed and admin-authenticated, and it holds each service's own
// token server-side, so the browser gains the capability without the credential.
//
// The explicit VITE_* overrides still win, for a dev pointing at services by hand.
const VISIOND_BASE_URL =
  (import.meta.env.VITE_DOOR_VISIOND_BASE_URL as string | undefined) ||
  `${API_BASE}/admin/visiond`;

const MEDIA_BASE_URL =
  (import.meta.env.VITE_DOOR_MEDIA_BASE_URL as string | undefined) ||
  `${API_BASE}/admin/door-media`;

async function request<T>(
  baseUrl: string,
  path: string,
  options: {
    method?: string;
    body?: unknown;
    adminToken?: string;
    isFormData?: boolean;
    formData?: FormData;
  } = {}
): Promise<T> {
  const headers: Record<string, string> = {};
  if (!options.isFormData) {
    headers["Content-Type"] = "application/json";
  }
  if (options.adminToken) {
    headers.Authorization = `Bearer ${options.adminToken}`;
  }

  const fetchOptions: RequestInit = {
    method: options.method || "GET",
    headers,
  };

  if (options.isFormData) {
    fetchOptions.body = options.formData;
  } else if (options.body !== undefined) {
    fetchOptions.body = JSON.stringify(options.body);
  }

  const resp = await fetch(`${baseUrl}${path}`, fetchOptions);

  if (!resp.ok) {
    let code = "unknown_error";
    let message = `request failed with status ${resp.status}`;
    try {
      const parsed = await resp.json();
      const errDetail = parsed?.detail;
      if (typeof errDetail === "string") {
        message = errDetail;
      } else if (errDetail && typeof errDetail === "object") {
        message = errDetail.error ?? JSON.stringify(errDetail);
        code = errDetail.error ?? code;
      }
    } catch {
      // ignore
    }
    throw new ApiError(resp.status, code, message);
  }

  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export interface EnrolledPerson {
  person_id: string;
  display_name: string;
  consent_version: string;
  consent_at: string;
  created_at: string;
  profile_id: string;
  color: string;
  sound: string | null;
}

/** A minted remote-enrollment invite (ADR-0016 §4). */
export interface CreatedInvite {
  invite_id: string;
  /** Single-use enrollment URL, secret in the path and key fingerprint in the fragment. */
  url: string;
  expires_at: string;
  max_images: number;
  relay_configured: boolean;
  door_key_fingerprint: string;
}

export interface InviteSummary {
  invite_id: string;
  label: string | null;
  max_images: number;
  created_at: string;
  expires_at: string;
  consumed_at: string | null;
  revoked_at: string | null;
  person_id: string | null;
  status: "open" | "consumed" | "expired" | "revoked";
}

export interface RelayStatus {
  configured: boolean;
  status: "disabled" | "stopped" | "ok" | "degraded";
  polls_ok?: number;
  polls_failed?: number;
  bundles_enrolled?: number;
  bundles_rejected?: number;
  consecutive_failures?: number;
  last_error?: string | null;
  last_success_at?: string | null;
}

/** One arrival, from door-visiond's visit log (ADR-0018 §1). Admin-only. */
export interface Visit {
  visit_id: string;
  person_id: string;
  display_name: string;
  arrived_at: string;
  last_seen_at: string;
}

export interface VisitCount {
  person_id: string;
  display_name: string;
  visits: number;
  last_seen_at: string;
}

export const enrollmentApi = {
  async getPeople(token: string): Promise<EnrolledPerson[]> {
    return request<EnrolledPerson[]>(VISIOND_BASE_URL, "/people", { adminToken: token });
  },

  async getConsent(): Promise<{ text: string; version: string }> {
    return request<{ text: string; version: string }>(VISIOND_BASE_URL, "/consent");
  },

  /** door-visiond's health, for the enrollment panel's privacy toggle. */
  async getVisiondHealth(adminToken: string): Promise<{ privacy_enabled: boolean }> {
    return request<{ privacy_enabled: boolean }>(VISIOND_BASE_URL, "/health", { adminToken });
  },

  async enroll(
    token: string,
    formData: FormData
  ): Promise<{ person_id: string; embeddings_created: number; quality: number[] }> {
    return request<{ person_id: string; embeddings_created: number; quality: number[] }>(
      VISIOND_BASE_URL,
      "/enroll",
      {
        method: "POST",
        adminToken: token,
        isFormData: true,
        formData,
      }
    );
  },

  async unenroll(token: string, personId: string): Promise<{ deleted: boolean }> {
    return request<{ deleted: boolean }>(VISIOND_BASE_URL, "/unenroll", {
      method: "POST",
      adminToken: token,
      body: { person_id: personId },
    });
  },

  async setPrivacyMode(token: string, enabled: boolean): Promise<{ enabled: boolean }> {
    return request<{ enabled: boolean }>(VISIOND_BASE_URL, "/privacy-mode", {
      method: "POST",
      adminToken: token,
      body: { enabled, changed_by: "admin" },
    });
  },

  /**
   * Mint a single-use phone-enrollment invite.
   *
   * The returned URL is the only copy of the invite secret — it is not stored on
   * the door and cannot be retrieved again. Losing it means minting a new invite.
   */
  async createInvite(token: string, label?: string): Promise<CreatedInvite> {
    return request<CreatedInvite>(VISIOND_BASE_URL, "/invites", {
      method: "POST",
      adminToken: token,
      body: { label: label && label.trim().length > 0 ? label.trim() : null },
    });
  },

  async listInvites(token: string, includeClosed = false): Promise<InviteSummary[]> {
    const query = includeClosed ? "?include_closed=true" : "";
    return request<InviteSummary[]>(VISIOND_BASE_URL, `/invites${query}`, { adminToken: token });
  },

  async revokeInvite(token: string, inviteId: string): Promise<{ revoked: boolean }> {
    return request<{ revoked: boolean }>(
      VISIOND_BASE_URL,
      `/invites/${encodeURIComponent(inviteId)}/revoke`,
      { method: "POST", adminToken: token }
    );
  },

  async getRelayStatus(token: string): Promise<RelayStatus> {
    return request<RelayStatus>(VISIOND_BASE_URL, "/relay-status", { adminToken: token });
  },

  /**
   * Arrival history. Admin-only by design — this is presence data, and ADR-0005 §5
   * keeps visitor logs off public routes (ADR-0018 E-24).
   */
  async getVisits(token: string, personId?: string): Promise<Visit[]> {
    const query = personId ? `?person_id=${encodeURIComponent(personId)}` : "";
    return request<Visit[]>(VISIOND_BASE_URL, `/visits${query}`, { adminToken: token });
  },

  async getVisitCounts(token: string): Promise<VisitCount[]> {
    return request<VisitCount[]>(VISIOND_BASE_URL, "/visits/counts", { adminToken: token });
  },

  /** Forget arrival history without unenrolling. Unenroll already cascades. */
  async purgeVisits(token: string, personId?: string): Promise<{ deleted: number }> {
    const query = personId ? `?person_id=${encodeURIComponent(personId)}` : "";
    return request<{ deleted: number }>(VISIOND_BASE_URL, `/visits/purge${query}`, {
      method: "POST",
      adminToken: token,
    });
  },

  async captureSnapshot(token: string): Promise<Blob> {
    const headers: Record<string, string> = {};
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    const resp = await fetch(`${MEDIA_BASE_URL}/snapshot`, { headers });
    if (!resp.ok) {
      throw new Error(`Snapshot capture failed with status ${resp.status}`);
    }
    return resp.blob();
  },

  async getCurrentVisitor(token: string): Promise<{ person_id: string; display_name: string } | null> {
    const headers: Record<string, string> = {};
    if (token) {
      headers.Authorization = `Bearer ${token}`;
    }
    const resp = await fetch(`${VISIOND_BASE_URL}/current-visitor`, { headers });
    if (resp.status === 204) {
      return null;
    }
    if (!resp.ok) {
      throw new Error("Failed to fetch current visitor");
    }
    return resp.json();
  }
};
