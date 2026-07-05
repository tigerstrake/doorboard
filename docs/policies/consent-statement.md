# Face-recognition consent statement

**Version: v1** — this version tag is recorded as `consent_version` at enrollment (ADR-0009 E-7). Any wording change bumps the version; both the enrollment CLI and the admin UI must render this file verbatim and never paraphrase it.

---

By enrolling, I confirm that:

- I am enrolling **my own face**, voluntarily, and I am the person shown in the captured images.
- The system will store a small set of **numerical face templates** (embeddings) and my chosen display name **on the door device's local storage**. My raw enrollment photos are deleted immediately after processing.
- Recognition is used **only to personalize** the door's greeting, lights, and sounds. It never unlocks anything, never makes security decisions, and never leaves the local network.
- I can **revoke consent at any time** via the admin interface or by asking the household admin. Revocation deletes my face templates immediately and irreversibly.
- If I am not recognized (or recognition is off), the door simply treats me as a guest — nothing about me is recorded.

Declining to enroll has no consequence other than receiving the generic greeting.
