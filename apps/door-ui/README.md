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

## Kiosk deployment

Two Chromium instances launched by systemd after door-api is healthy (deploy/pi-door). Screens map: HDMI-1 → `/wallboard`, HDMI-2 → `/doorpad`.
