# packages/auth — tokens and admin authentication

Security-sensitive: changes here always get Claude-tier review (ADR-0005/0008).

- **Admin auth:** session-based login for `/admin` routes on the Pi and NUC (single-owner model; simple credential + session cookie, rate-limited, no public signup).
- **Service tokens:** issuance/verification of limited-scope tokens — the Pi's upload token, the ingest token door-sync presents to control-plane-api. Per-device, revocable from the NUC, rotation documented in a runbook.
- **Visitor tokens:** short-lived signed tokens embedded in QR codes for `/visitor` flows; scoped to one session, minutes-long TTL, rate-limited verification.
- **Rules:** no secrets in git; tokens never logged; constant-time comparisons; the door Pi can *hold* only its own limited tokens (trust model §2). Mutual TLS is a future ADR if warranted — don't build speculative PKI.
