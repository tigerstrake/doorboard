# door-ui — kiosk SPA (Wallboard, DoorPad, visitor, admin)

**Plane:** real-time door · **Host:** door Pi 5, two Chromium kiosks · **Language:** TypeScript/React · **Tasks:** T-004, T-202, T-402, T-404, T-304

One Vite/React app, four surfaces (full specs in [docs/ui/](../../docs/ui/)):

| Route | Screen | Audience |
|---|---|---|
| `/wallboard` | 21.5–24" monitor | public — ambient dashboard, flips to visitor mode |
| `/doorpad` | 7" touchscreen | public — ring, video message, guestbook, poll, check-in |
| `/visitor` | phone via QR | public — tokenized, rate-limited |
| `/admin` (+`/diagnostics`) | any browser, authenticated | owner only |

## Hard requirements

- **Local-only dependency:** all public routes work against Pi-local APIs alone (NUC down = ambient tiles show cached/last-known data with a subtle staleness hint, everything else works).
- **Latency:** touchscreen tap → visible response p95 < 100 ms (optimistic local rendering; WebSocket round-trips are not allowed to gate feedback).
- **Kiosk resilience:** auto-reconnect WebSockets, re-render from server snapshot, session rejoin on reload, automatic reset to idle after visitor flows.
- **Privacy:** public routes never render names of unenrolled people, exact locations, calendars, full visitor logs, diagnostics, or private photos. All user-generated content is escaped — no `dangerouslySetInnerHTML` with user data, ever.
- **Input model:** DoorPad is large-buttons-only, no keyboard-heavy flows; hardware bell stays the primary ring path.
- Shared components in `packages/ui-kit`; events via `packages/event-client`; types generated from contracts.

## Body Surfers (ADR-0049)

`pnpm build` also builds [`apps/body-surfers`](../body-surfers/) and copies it to `dist/games/body-surfers/` (`scripts/copy-games.mjs`; `pnpm dev` copies it if it is already built). The wallboard frames it from the other loopback name, so it runs in its own renderer process. Logic lives in `src/bodySurfers.ts` and `src/wallboard/BodySurfersFrame.tsx`.

| Variable | Default | Effect |
|---|---|---|
| `VITE_FEATURE_BODY_SURFERS` | on | `false` removes the DoorPad tile, the wallboard frame and the admin clear |
| `VITE_BODY_SURFERS_MEDIA_URL` | `http://127.0.0.1:8082/streams` | door-media's stream list, read by the game |
| `VITE_BODY_SURFERS_URL` | the bundled game | full page URL, e.g. the game's own dev server; its query is kept, so `?webcam=1` tests door mode with a laptop camera |

## Kiosk deployment

Two Chromium instances launched by systemd after door-api is healthy (deploy/pi-door). Screens map: HDMI-1 → `/wallboard`, HDMI-2 → `/doorpad`.

Build production bundles with `VITE_AMBIENT_MOCK=false`. `VITE_AIRCRAFT_ALERT_DISTANCE_KM`
sets the hallway hero-alert threshold using the distance already present in
`ambient.aircraft_summary`; it does not add route, destination, or location fields. Mock
ambient fixtures are limited to development and visual tests.
