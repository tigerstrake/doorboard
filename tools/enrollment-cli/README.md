# tools/enrollment-cli

Owner-side CLI for face enrollment (T-304 UI + T-302 pipeline; CLI is the scriptable path). Runs on the Pi over SSH or locally: confirm consent (recorded), capture guided image set (varied lighting/angle), generate embeddings via door-visiond's enroll endpoint, assign display profile (name, color, optional sound), test-match, unenroll/revoke with immediate embedding deletion. Never exposed on public routes; biometric files never leave the Pi's SSD.
