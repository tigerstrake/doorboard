import { safeRandomUUID } from "./uuid";

export const WALLBOARD_FOCUS_TIMEOUT_MS = 120000;
export const WALLBOARD_CONTROL_STORAGE_KEY = "doorboard_wallboard_focus_request";
export const WALLBOARD_CONTROL_EVENT = "doorboard-wallboard-focus";

export type WallboardFocusChannel =
  | "aircraft"
  | "satellite"
  | "scoreboard"
  | "birds"
  | "printer"
  | "food"
  | "poll"
  | "guestbook"
  | "moments";

export interface WallboardFocusRequest {
  requestId: string;
  mode: "ambient" | "focus";
  channel: WallboardFocusChannel | null;
  requestedAt: number;
  expiresAt: number | null;
}

export interface WallboardChannelDefinition {
  id: "ambient" | WallboardFocusChannel;
  title: string;
  eyebrow: string;
  description: string;
}

export const WALLBOARD_CHANNELS: WallboardChannelDefinition[] = [
  {
    id: "ambient",
    title: "Ambient grid",
    eyebrow: "Default",
    description: "Return the hallway display to the full dashboard.",
  },
  {
    id: "aircraft",
    title: "Flights",
    eyebrow: "Aircraft",
    description: "Nearby aircraft, altitude, and distance.",
  },
  {
    id: "satellite",
    title: "Satellite pass",
    eyebrow: "Sky",
    description: "Next visible pass and rise direction.",
  },
  {
    id: "scoreboard",
    title: "Scoreboard",
    eyebrow: "Room",
    description: "Resident scoreboard at hallway scale.",
  },
  {
    id: "birds",
    title: "Birds",
    eyebrow: "Ambient",
    description: "Today's detected species summary.",
  },
  {
    id: "printer",
    title: "Printer",
    eyebrow: "Lab",
    description: "3D printer state and progress.",
  },
  {
    id: "food",
    title: "Food",
    eyebrow: "Daily",
    description: "Current food recommendation.",
  },
  {
    id: "poll",
    title: "Poll",
    eyebrow: "Social",
    description: "Current poll and visible results.",
  },
  {
    id: "guestbook",
    title: "Guestbook",
    eyebrow: "Social",
    description: "Approved notes only.",
  },
  {
    id: "moments",
    title: "Moments",
    eyebrow: "Gallery",
    description: "Approved photo-booth highlights.",
  },
];

export function createWallboardFocusRequest(
  channel: "ambient" | WallboardFocusChannel
): WallboardFocusRequest {
  const now = Date.now();
  return {
    requestId: safeRandomUUID(),
    mode: channel === "ambient" ? "ambient" : "focus",
    channel: channel === "ambient" ? null : channel,
    requestedAt: now,
    expiresAt: channel === "ambient" ? null : now + WALLBOARD_FOCUS_TIMEOUT_MS,
  };
}

export function isWallboardFocusRequest(value: unknown): value is WallboardFocusRequest {
  if (!value || typeof value !== "object") return false;
  const candidate = value as Partial<WallboardFocusRequest>;
  if (candidate.mode !== "ambient" && candidate.mode !== "focus") return false;
  if (candidate.mode === "ambient") return candidate.channel === null;
  return WALLBOARD_CHANNELS.some((channel) => channel.id === candidate.channel);
}
