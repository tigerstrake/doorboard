# ADR-0038: poll a metered service only when there is work

**Status:** Accepted · **Date:** 2026-08-22

## Context

door-visiond polls the public relay for sealed enrollment bundles. The loop ran every 5 seconds
for the life of the process, unconditionally.

Measured on the live door: **74,187 polls since the last restart, `relay_bundles_enrolled = 0`.**
Roughly 17,000 relay calls a day, each one a metered Vercel function invocation, producing
nothing. It had consumed **75% of the 1,000,000/month free tier**, and the consequence of
reaching 100% is that Vercel *pauses the deployment*.

Nothing failed. Nothing logged a warning. `polls_ok` climbed and `polls_failed` stayed at 7. The
first signal was a billing email.

The periodic resync was a smaller instance of the same thing: republishing the door key every 300
seconds whether or not anything had changed, 288 needless calls a day, while `request_resync()`
already fired on real changes like key rotation.

## Decision

**Poll only when a pickup is possible.** A bundle can only exist if an invite is open, so the gate
is `relay_has_open_invites()` — a cheap `SELECT 1 ... LIMIT 1` over the same three conditions the
registration query already uses (unconsumed, unrevoked, unexpired).

An idle door now makes **zero** relay calls. Polling stays at 5 seconds while an invite is open,
because that is when latency is actually felt — the visitor is standing there watching a page.

**Plus a grace window** (`VISIOND_RELAY_IDLE_GRACE_S`, 300s). An invite is consumed the instant a
bundle is claimed, so stopping dead on consumption could strand a bundle that arrived in the same
breath. The grace also covers a submission landing microseconds before expiry.

**Periodic resync moves from 300s to 3600s.** `request_resync()` already covers real changes, so
the periodic pass is belt-and-braces rather than the mechanism.

**A skip counter is exposed** (`relay_polls_skipped_idle`). The gate working looks exactly like the
gate being broken — no traffic either way — so the number that *would* have been billed is worth
being able to see.

### Rate limiting is not a fix

The tempting response was to slow the interval down, and that was applied first as immediate
mitigation. It is not a solution: an ungated loop is unbounded in *time*, so any interval
eventually reaches any cap. 60s would have burned ~43,000/month forever, for nothing. Only a
demand gate makes the idle cost zero.

## Enforcement

`tests/test_external_poll_budget.py` enumerates every timer-driven outbound call as a table of
`(setting, target, metered, demand_gated, note)` and asserts:

1. **Every `*_INTERVAL_S` / `*_REFRESH_S` setting across the three services appears in the table.**
   A new poller cannot be added without someone writing down what it costs. This immediately
   caught one nobody had thought about (`DOOR_VISIOND_PURGE_WORKER_INTERVAL_S` — harmless, it is
   loopback and drains an outbox, but it was undeclared).
2. **Anything metered must be demand-gated.** Not "must be slow" — see above.
3. **Ungated pollers must stay inside a stated monthly budget**, computed from their interval
   rather than asserted by hand.

The failure mode this prevents is not a bug in the ordinary sense: the code worked exactly as
written. What was missing was any place where the *cost* of a background loop was written down and
reviewable.

## Consequences

- Remote enrollment behaves identically from a visitor's point of view; the fast poll is present
  for the whole time it matters.
- One more protocol method on `RelayHandler`, so a test double that omits it fails loudly rather
  than silently never polling.
- The table needs updating when a poller is added, which is the intended friction.
- **The largest remaining ungated external poller is the aircraft feed** at 30s — roughly
  86,000/month to adsb.fi, running whether or not anyone is looking at the wallboard. adsb.fi is
  not metered and this is inside its stated limits, so there is no hard failure, but it is
  recorded in the table as the obvious next candidate for on-demand gating.
- Usage already accrued this billing period does not go away. If the cap is reached, the relay
  pauses and the door is unaffected — the relay is off the critical path by design (ADR-0002), and
  door-visiond simply logs `relay_poll_failed`. Only remote enrollment and the visitor QR flow
  stop.
