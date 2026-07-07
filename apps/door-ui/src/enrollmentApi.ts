import { ApiError } from "./socialApi";

const VISIOND_BASE_URL =
  (import.meta.env.VITE_DOOR_VISIOND_BASE_URL as string | undefined) ||
  `http://${window.location.hostname}:8081`;

const MEDIA_BASE_URL =
  (import.meta.env.VITE_DOOR_MEDIA_BASE_URL as string | undefined) ||
  `http://${window.location.hostname}:8082`;

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

export const enrollmentApi = {
  async getPeople(token: string): Promise<EnrolledPerson[]> {
    return request<EnrolledPerson[]>(VISIOND_BASE_URL, "/people", { adminToken: token });
  },

  async getConsent(): Promise<{ text: string; version: string }> {
    return request<{ text: string; version: string }>(VISIOND_BASE_URL, "/consent");
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
