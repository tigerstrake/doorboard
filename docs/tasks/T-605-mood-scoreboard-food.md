# T-605: Mood, scoreboard, food provider + tiles

**Agent:** gemini · **Milestone:** M6 · **Depends on:** T-501, T-404

## Context

Handoff §13 (mood/scoreboards, food); spec: [integrations/food-recommendation/README.md](../../integrations/food-recommendation/README.md). Small social features + one provider interface.

## Deliverables

- Mood: check-in endpoint (owner/roommate via admin UI or HA shortcut), configured mood set, `social.mood_updated`, wallboard tile data.
- Scoreboard: boards with entries/scores, admin CRUD, casual bet notes (sanitized like all UGC per T-403 patterns), `social.scoreboard_updated`.
- Food: `FoodRecommendationProvider` interface exactly as specced; `mock` provider (deterministic-per-date fixture rotation); daily wallboard-worker job → `ambient.food_recommendation`; cached with yesterday-fallback + staleness marker.
- Admin panels for all three; durable storage in control-plane Postgres; deletion paths.

## Out of scope

The real personal food system (future brief against this interface — do not attempt to reverse-engineer it), new tile layouts (fill T-004's tiles), doorpad surfaces.

## Acceptance criteria

- All three tiles live end-to-end in the simulated stack; mock food pick changes daily and is stable within a day.
- Scoreboard/mood content passes the same injection corpus as T-403.
- Provider failure → yesterday's recommendation + staleness marker (tested).
