# Face-recognition consent statement

**Version: v2** — this version tag is recorded as `consent_version` at enrollment (ADR-0009 E-7). Any wording change bumps the version; the enrollment CLI, the admin UI, and the phone enrollment page must render this file verbatim and never paraphrase it.

---

By enrolling, I confirm that:

- I am enrolling **my own face**, voluntarily, and I am the person shown in the captured images.
- The system will store a small set of **numerical face templates** (embeddings) and my chosen display name **on the door device's local storage**. My raw enrollment photos are deleted immediately after processing.
- Recognition is used **only to personalize** the door's greeting, lights, and sounds. It never unlocks anything and never makes security decisions.
- My face templates stay on the door device. They are never uploaded, and recognition itself runs entirely on the local network.
- I can **revoke consent at any time** via the admin interface or by asking the household admin. Revocation deletes my face templates immediately and irreversibly.
- If I am not recognized (or recognition is off), the door simply treats me as a guest — nothing about me is recorded.

Declining to enroll has no consequence other than receiving the generic greeting.

## The two ways to enroll

Enrolling at the door and enrolling from a phone differ in one respect worth understanding.

**At the door.** Photos are taken by the door's own camera and never travel anywhere. Nothing about my enrollment touches the internet.

**From my phone, using the QR code.** My photos are taken by my phone and have to reach the door device, so they travel over the internet through a relay website. Before they leave my phone:

- They are **encrypted on my phone**, using a key only the door device can undo. My display name is encrypted the same way.
- The relay stores only that encrypted data. It **cannot read my photos or my name** and holds no key that could.
- The door device collects the encrypted data, decrypts it locally, creates the face templates, and the encrypted copy is deleted. Anything left uncollected is deleted automatically within 15 minutes.
- The relay does learn that *an* enrollment happened and roughly when — but not who, and nothing about what the photos contain.

One honest caveat: because the phone page is a website, I am trusting that its code has not been tampered with at the moment I load it. If I would rather not rely on that, I can **enroll at the door instead** — that path sends nothing over the internet at all and produces exactly the same result.
