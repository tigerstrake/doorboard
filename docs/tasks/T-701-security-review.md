# T-701: Security review + deployment checklist

**Agent:** claude · **Milestone:** M7 · **Depends on:** all M0–M6
**Why this agent:** adversarial whole-system review requires judgment and the trust-model context.

## Context

Handoff §16 and the trust model (ARCHITECTURE.md §2). The Pi hangs on a hallway door: assume it gets unplugged, rebooted, or stolen.

## Deliverables

- Full review against §16: route separation (public/admin), no raw MediaMTX/RTSP/SSH/Hailo exposure, no public-internet exposure, VLAN/firewall recommendations for the actual network, per-device credentials verified, token rotation working (drill it), UGC sanitization spot-audit (fresh injection corpus), wallboard untrusted-HTML check.
- Stolen-Pi drill: walk the T-301 threat model against the real system — enumerate actual SSD contents, verify credential scope claims by attempting cross-scope operations with the Pi's tokens, execute the revocation runbook, measure time-to-revoke.
- Physical review with the owner: enclosure lock, USB/microSD accessibility, watchdog/restart behavior on power-cycle.
- Output: `docs/runbooks/security-checklist.md` — the pre-deployment checklist (every line verified, dated) + issues filed for every finding, severity-labeled; blocking findings gate T-790.

## Out of scope

Fixing findings (separate issues, assigned by severity to the right tier), penetration testing beyond the system's own threat model, security theater (no speculative PKI — findings must trace to the trust model).

## Acceptance criteria

- Checklist merged with every item verified or converted to a blocking issue.
- Stolen-Pi drill documented with measured revocation time and enumerated residual data — matches ADR-0007's claim or ADRs get corrected.
- Zero credentials/secrets found in git history, images, config bundles, or Pi filesystem outside its scoped set (scripted scan, results attached).

## Carry-forward from T-690 (M6 review)

Ambient-adapter network-egress surface to include in the review (all NUC-side in `wallboard-worker`; none on the Pi):

| Adapter | Outbound | Auth | Timeout |
|---|---|---|---|
| BirdNET | BirdNET-Go HTTP (local) | none | 5s |
| Satellites | Celestrak TLE fetch | none | fetch + 7-day cache |
| Aircraft | OpenSky `states/all` | optional basic (user/pass) | 10s |
| Printer | OctoPrint `/api/job` (read-only) | `X-Api-Key` | 5s |
| Food | none (local deterministic) | — | — |

M6 review confirmed: integration credentials live only in `wallboard-worker` settings (never on the Pi), all real providers have mock counterparts, and no ambient payload carries PII/biometrics. T-701 should still adversarially re-verify credential scope and check for egress to the public internet from the Pi's own services.
