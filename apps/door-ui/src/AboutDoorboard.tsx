/**
 * What this door is, in the visitor's terms.
 *
 * The doorpad's version of this was two sentences under the heading "Camera Notice &
 * Deletion Requests" — accurate, and it told a visitor almost nothing about a device
 * with two cameras pointed at them. Written once here and rendered on both the doorpad
 * (as a screen) and the wallboard (as a focusable channel), so the hallway and the panel
 * cannot end up describing the door differently.
 *
 * The claims below are deliberately the *invariants*, not the feature list: they are the
 * things ARCHITECTURE.md §9 and ADR-0009 actually enforce, so this stays true as
 * features come and go. Anything softer belongs in the feature copy, not here.
 */

export interface AboutSection {
  heading: string;
  body: string;
}

export const ABOUT_SECTIONS: AboutSection[] = [
  {
    heading: "What it is",
    body:
      "A doorbell that runs entirely in this building. Press Ring Bell and the light and " +
      "chime fire from a microcontroller in the door itself, so it answers in well under " +
      "a tenth of a second even if the network is down.",
  },
  {
    heading: "The cameras",
    body:
      "Two: one frames the doorway for live video when the bell rings, one is used to " +
      "recognise people who have chosen to be recognised. Recognition runs on a chip in " +
      "this door. No image or video of you is sent to a cloud service.",
  },
  {
    heading: "If you are not enrolled",
    body:
      "You are not identified, not named, and not remembered. The door computes nothing " +
      "durable about an unrecognised face — no template is stored, no 'seen before' list " +
      "exists to be added to. You get a generic greeting, which is the whole of it.",
  },
  {
    heading: "If you enroll",
    body:
      "You choose it, you confirm a consent notice, and you pick your own colour. After " +
      "that the door greets you by name and lights up in your colour. You can be removed " +
      "in a single action, and removal destroys the face data immediately rather than " +
      "marking it hidden.",
  },
  {
    heading: "Being recognised is not a key",
    body:
      "It changes a greeting and a colour. It never unlocks anything, grants anything, or " +
      "gates anything — the door has no lock to open. That is enforced in the build, not " +
      "just intended: nothing in the code that decides access is allowed to read who you are.",
  },
  {
    heading: "What you leave here",
    body:
      "Guestbook notes, poll votes and check-ins are voluntary and yours to delete. If the " +
      "door recognises you it tells you before you write that your name will be attached, " +
      "so an attributed note is never a surprise.",
  },
];

export function AboutDoorboard({ className = "" }: { className?: string }) {
  return (
    <div className={`about-doorboard ${className}`} data-testid="about-doorboard">
      {ABOUT_SECTIONS.map((section) => (
        <section key={section.heading} className="about-doorboard__section">
          <h3 className="about-doorboard__heading">{section.heading}</h3>
          <p className="about-doorboard__body">{section.body}</p>
        </section>
      ))}
    </div>
  );
}
