# Deployment security checklist (T-701)

**Reviewer:** Claude tier · **Date:** 2026-07-08 · **Scope:** whole system at `main` (post-M6)
**Basis:** handoff §16 (security requirements), §3.2 (physical security), §5 (trust boundaries); ARCHITECTURE.md §2 (trust model); ADR-0007 (storage tiers), ADR-0009 (deletion).

**Verdict:** ✅ **No blocking findings at the code/config level** — T-790 is not gated on security grounds. Remaining items are network/physical controls that can only be verified against real hardware at deployment time (marked ⏸); they are tracked, not resolved.

Legend: ✅ verified in code/config now · ☐ verify at deployment (network/host) · ⏸ deferred — requires physical hardware.

## §16 security requirements

| # | Requirement | Status | Evidence / action |
|---|---|---|---|
| 1 | Public and admin routes separated | ✅ | `control-plane-api` `_require_admin` gates all `/admin/*` via `secrets.compare_digest(presented, cfg.admin_token)`; door-api visitor routes are token-scoped, admin routes separate + authenticated. |
| 2 | Don't expose raw MediaMTX / RTSP / SSH / Hailo broadly | ☐ | Services bind to internal interfaces; MediaMTX consumed locally via WebRTC. **At deployment:** firewall these ports to the door VLAN; no port-forwards; SSH key-only, LAN-scoped. |
| 3 | No direct public-internet exposure | ☐ | No service opens an inbound public listener in code. **At deployment:** confirm the router has no port-forwards to Pi/NUC; outbound-only for ambient adapters. |
| 4 | VLAN / firewall segmentation | ☐ | **At deployment:** put the door plane on its own VLAN; allow only Pi→NUC (control-plane + sync) and Pi→NAS (scoped upload); deny door-plane→WAN except the known ambient egress (below). |
| 5 | Device-specific credentials | ✅ | `control-plane-api` issues **scoped** service tokens (`ingest` / `config` / Pi-scoped) via `POST /admin/tokens`; the Pi holds only its scoped set, never the admin token. |
| 6 | No secrets in git | ✅ | Working tree + full history scanned: zero `.env` / `.pem` / `.key` / credential files ever committed; no high-entropy secret assignments in source. CI enforces the file check on every PR. |
| 7 | `.env.example` only | ✅ | `.env.example` present; `.env` is git-ignored. |
| 8 | Rotate tokens | ✅ | `POST /admin/tokens` (issue) + `DELETE /admin/tokens/{id}` (revoke) + list; procedure in [token-rotation.md](token-rotation.md). |
| 9 | Tokenized / rate-limited QR endpoints | ✅ | Visitor URLs carry HMAC-signed, short-lived tokens (`hmac` + `compare_digest`); social writes rate-limited (5 / 60 s default) via `social/ratelimit.py`. |
| 10 | Sanitize user-generated content | ✅ | `social/sanitize.py` + rejection paths in `social/service.py`; invalid/oversized/injection inputs rejected (rejection counters emitted). |
| 11 | Prevent wallboard untrusted HTML | ✅ | No `dangerouslySetInnerHTML` / `innerHTML` with user data anywhere in `door-ui`/`ui-kit`; `SocialRenderers.tsx` builds text nodes; `social.test.tsx` asserts `<script>` payloads do not execute. |
| 12 | Deployment security checklist | ✅ | This document. |

## Trust-boundary confirmations (ARCHITECTURE.md §2)

- ✅ **No admin credentials or long-term archive on the door Pi** — Pi holds only scoped tokens; archive lives on NAS (ADR-0007). Verified: no door-Pi service imports admin secrets or ambient-integration credentials.
- ✅ **ESP32 holds no secrets** — profile cache is opaque IDs + monotonic expiry only.
- ✅ **Public screens show broad status only** — door-ui privacy rules keep names of unenrolled people, locations, calendars, diagnostics, and private photos off public routes.
- ✅ **Ambient egress is outbound-only and credential-scoped to the NUC** (see inventory below).

## Ambient network-egress inventory (from T-690)

| Adapter (NUC-side) | Outbound | Auth | Timeout |
|---|---|---|---|
| BirdNET | BirdNET-Go HTTP (local) | none | 5 s |
| Satellites | Celestrak TLE fetch | none | fetch + 7-day cache |
| Aircraft | OpenSky `states/all` | optional basic | 10 s |
| Printer | OctoPrint `/api/job` (read-only) | `X-Api-Key` | 5 s |
| Food | none (local) | — | — |

Credentials live only in `wallboard-worker` settings on the NUC. **At deployment:** allow the NUC egress only to these hosts.

## Stolen-Pi drill ⏸ (deferred — requires hardware; runbook ready)

Procedure to execute on real hardware (documents the T-301 threat model against the built system):
1. Enumerate actual SSD contents (enrollment DB, privacy state, pending media) and confirm no admin secrets / no NAS-admin credential present.
2. Attempt cross-scope operations with the Pi's tokens against the NUC — expect `401/403` for anything beyond `ingest`/`config` scope.
3. Execute [token-rotation.md](token-rotation.md) revocation; **measure time-to-revoke** and confirm the stolen tokens stop working.
4. Confirm the outcome matches ADR-0007/ADR-0009 residual-data claims, or file a correction.

## Physical review ⏸ (deferred — requires hardware, with owner)

- Enclosure lock; USB/microSD not accessible without opening; watchdog restart on power-cycle; offline boot to operational state.

## Findings

**None blocking.** No issues filed. Deployment-time (☐) and hardware (⏸) items above are the pre-go-live actions; they are network/physical controls, not code defects, and do not block T-790's software sign-off. This checklist should be re-walked with every line dated ✅ on the real network/hardware before the system faces a real hallway.
