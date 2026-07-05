export const presenceFixture = {
  owner: {
    label: "available" as const,
    occurred_at: "2026-07-04T12:34:56.123Z",
  },
  roommate: {
    label: "busy" as const,
    occurred_at: "2026-07-04T12:30:56.123Z",
  },
};

export const birdFixture = {
  occurred_at: "2026-07-04T12:34:56.123Z",
  total_detections: 7,
  window: "today",
  top_species: [
    {
      name: "House Finch",
      count: 4,
      confidence_avg: 0.88,
    },
    {
      name: "Mourning Dove",
      count: 2,
      confidence_avg: 0.79,
    },
  ],
};

export const aircraftFixture = {
  occurred_at: "2026-07-04T12:34:56.123Z",
  nearby: [
    {
      callsign: "UAL123",
      altitude_ft: 32000,
      distance_km: 18.2,
      heading: 94,
    },
    {
      callsign: "SWA456",
      altitude_ft: 12000,
      distance_km: 8.5,
      heading: 270,
    },
  ],
};

export const satelliteFixture = {
  occurred_at: "2026-07-04T12:34:56.123Z",
  satellite: "ISS",
  visible: true,
  rise_at: new Date(Date.now() + 600000).toISOString(), // 10 minutes in future
  direction: "NW",
  max_elevation_deg: 64.5,
};

export const printerFixture = {
  occurred_at: "2026-07-04T12:34:56.123Z",
  state: "printing",
  job_name: "door-bracket",
  progress_pct: 42.0,
  eta: new Date(Date.now() + 900000).toISOString(), // 15 minutes in future
};

export const moodFixture = {
  occurred_at: "2026-07-04T12:34:56.123Z",
  mood: "focused",
  subject_id: "owner",
};

export const scoreboardFixture = {
  occurred_at: "2026-07-04T12:34:56.123Z",
  board_id: "daily",
  scores: [
    { name: "Taylor (Owner)", score: 14 },
    { name: "Alex (Roommate)", score: 12 },
  ],
};

export const foodFixture = {
  occurred_at: "2026-07-04T12:34:56.123Z",
  title: "Noodle soup",
  detail: "Good between classes.",
  provider: "manual",
};

export const pollFixture = {
  question: "What should we name the door?",
  options: [
    { id: "1", text: "Door McDoorface", votes: 24 },
    { id: "2", text: "Smarty", votes: 12 },
    { id: "3", text: "The Portal", votes: 19 },
  ],
};

export const guestbookFixture = {
  occurred_at: "2026-07-04T12:34:56.123Z",
  entries: [
    { author: "Taylor", text: "Hello from the hallway." },
    { author: "Jordan", text: "Nice UI interface!" },
  ],
};
