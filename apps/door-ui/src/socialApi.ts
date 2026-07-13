// Client for door-api's guestbook/poll/checkin/deletion endpoints (T-403).
// Mirrors the DoorboardEventClient convention of defaulting to the current
// hostname for Pi-local networking (packages/event-client/src/index.ts).

const DOOR_API_BASE_URL =
  (import.meta.env.VITE_DOOR_API_BASE_URL as string | undefined) ||
  `http://${window.location.hostname}:8000`;

let runtimeVisitorToken: string | null = null;

function tokenFromUrl(): string | null {
  return new URLSearchParams(window.location.search).get("token");
}

export function setVisitorToken(token: string): void {
  runtimeVisitorToken = token;
}

export async function getSessionToken(): Promise<string> {
  const existing = tokenFromUrl() ?? runtimeVisitorToken;
  if (existing) return existing;
  const response = await request<{ token: string }>("/visitor-token");
  runtimeVisitorToken = response.token;
  return response.token;
}

export class ApiError extends Error {
  code: string;
  status: number;
  traceId: string | undefined;

  constructor(status: number, code: string, message: string, traceId?: string) {
    super(message);
    this.status = status;
    this.code = code;
    this.traceId = traceId;
  }
}

async function request<T>(
  path: string,
  options: { method?: string; body?: unknown; adminToken?: string } = {}
): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (options.adminToken) {
    headers.Authorization = `Bearer ${options.adminToken}`;
  }
  const resp = await fetch(`${DOOR_API_BASE_URL}${path}`, {
    method: options.method || "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (!resp.ok) {
    let code = "unknown_error";
    let message = `request failed with status ${resp.status}`;
    let traceId: string | undefined;
    try {
      const parsed = await resp.json();
      const err = parsed?.detail?.error ?? parsed?.error;
      if (err) {
        code = err.code ?? code;
        message = err.message ?? message;
        traceId = err.trace_id;
      }
    } catch {
      // Response body wasn't the expected error envelope — surface the status only.
    }
    throw new ApiError(resp.status, code, message, traceId);
  }

  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}

export interface GuestbookEntry {
  id: string;
  text: string;
  author_label: string | null;
  created_at: string;
  status?: "pending" | "approved" | "deleted";
  deleted_at?: string | null;
}

export interface PollOption {
  id: string;
  text: string;
}

export interface Poll {
  id: string;
  question: string;
  status: "open" | "closed";
  created_at: string;
  closed_at: string | null;
  options: PollOption[];
}

export interface Checkin {
  id: string;
  person_id: string | null;
  label: string | null;
  created_at: string;
}

export interface MostFrequentVisitorStat {
  person_id: string;
  label: string | null;
  count: number;
}

export interface PollResultRow {
  option_id: string;
  text: string;
  votes: number;
}

export interface ModerationLogEntry {
  id: string;
  target_kind: string;
  target_id: string;
  action: string;
  actor: string;
  created_at: string;
}

export interface VisitorSession {
  session_id: string;
  expires_at: number;
  state: SessionState;
}

type SessionState =
  | "IDLE"
  | "APPROACH_DETECTED"
  | "IDENTITY_CACHED"
  | "BUTTON_PRESSED"
  | "VISITOR_MODE"
  | "RINGING"
  | "ANSWERED"
  | "UNANSWERED_TIMEOUT"
  | "VIDEO_MESSAGE_OFFERED"
  | "VIDEO_MESSAGE_RECORDING"
  | "VIDEO_MESSAGE_REVIEW"
  | "VIDEO_MESSAGE_SAVED"
  | "SESSION_END";

export const socialApi = {
  setVisitorToken,

  async validateVisitorSession(): Promise<VisitorSession> {
    const token = await getSessionToken();
    return request<VisitorSession>(`/visitor-session?token=${encodeURIComponent(token)}`);
  },

  async createGuestbookEntry(text: string, authorLabel: string | null): Promise<GuestbookEntry> {
    const sessionToken = await getSessionToken();
    return request<GuestbookEntry>("/guestbook", {
      method: "POST",
      body: { text, author_label: authorLabel, session_token: sessionToken },
    });
  },

  async listGuestbook(limit = 20): Promise<GuestbookEntry[]> {
    const data = await request<{ entries: GuestbookEntry[] }>(`/guestbook?limit=${limit}`);
    return data.entries;
  },

  async getCurrentPoll(): Promise<Poll | null> {
    const data = await request<{ poll: Poll | null }>("/polls/current");
    return data.poll;
  },

  async getPollResults(pollId: string): Promise<PollResultRow[]> {
    const data = await request<{ results: PollResultRow[] }>(`/polls/${pollId}/results`);
    return data.results;
  },

  async castVote(pollId: string, optionId: string): Promise<void> {
    const sessionToken = await getSessionToken();
    await request(`/polls/${pollId}/vote`, {
      method: "POST",
      body: { option_id: optionId, session_token: sessionToken },
    });
  },

  // No person_id param: attribution is derived server-side from the current
  // session's cached identity (door-api SessionMachine), never trusted from
  // the client — see door_api/social/routes.py CheckinCreateRequest.
  async createCheckin(label: string | null): Promise<Checkin> {
    const sessionToken = await getSessionToken();
    return request<Checkin>("/checkins", {
      method: "POST",
      body: { label, session_token: sessionToken },
    });
  },

  async listCheckins(limit = 20): Promise<Checkin[]> {
    const data = await request<{ checkins: Checkin[] }>(`/checkins?limit=${limit}`);
    return data.checkins;
  },

  async mostFrequentVisitor(): Promise<MostFrequentVisitorStat | null> {
    const data = await request<{ stat: MostFrequentVisitorStat | null }>(
      "/checkins/stats/most-frequent"
    );
    return data.stat;
  },

  async requestDeletion(targetKind: string, targetId: string): Promise<void> {
    const sessionToken = await getSessionToken();
    await request("/social/deletion-requests", {
      method: "POST",
      body: { target_kind: targetKind, target_id: targetId, session_token: sessionToken },
    });
  },

  admin: {
    async listGuestbook(
      status: "pending" | "approved",
      adminToken: string
    ): Promise<GuestbookEntry[]> {
      const data = await request<{ entries: GuestbookEntry[] }>(
        `/admin/guestbook?status=${status}&limit=50`,
        { adminToken }
      );
      return data.entries;
    },

    async approveGuestbook(id: string, adminToken: string): Promise<void> {
      await request(`/admin/guestbook/${id}/approve`, { method: "POST", adminToken });
    },

    async deleteGuestbook(id: string, adminToken: string): Promise<void> {
      await request(`/admin/guestbook/${id}`, { method: "DELETE", adminToken });
    },

    async listPolls(adminToken: string): Promise<Poll[]> {
      const data = await request<{ polls: Poll[] }>("/admin/polls?limit=50", { adminToken });
      return data.polls;
    },

    async createPoll(question: string, options: string[], adminToken: string): Promise<Poll> {
      return request<Poll>("/admin/polls", {
        method: "POST",
        body: { question, options },
        adminToken,
      });
    },

    async closePoll(id: string, adminToken: string): Promise<void> {
      await request(`/admin/polls/${id}/close`, { method: "POST", adminToken });
    },

    async deleteCheckin(id: string, adminToken: string): Promise<void> {
      await request(`/admin/checkins/${id}`, { method: "DELETE", adminToken });
    },

    async moderationLog(adminToken: string): Promise<ModerationLogEntry[]> {
      const data = await request<{ entries: ModerationLogEntry[] }>(
        "/admin/social/moderation-log?limit=50",
        { adminToken }
      );
      return data.entries;
    },
  },
};
