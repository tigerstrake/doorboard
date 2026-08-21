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

import aboutStats from "./aboutStats.json";

/**
 * The numbers, for the focused view.
 *
 * The grid tile showed five stat chips and the focused channel — the one you get when you
 * deliberately open "About this doorboard" and stand there reading — showed none of them, so
 * choosing to look closer gave you *less*. These fill that in.
 *
 * Every value is generated from the tree by tools/project-stats/collect.py. None of it is
 * typed in by hand, which is the only reason it can be trusted after a few months: the
 * previous baked figures were from July and had drifted to half the real line count and a
 * third of the real ADR count.
 */
export interface AboutFact {
  value: string;
  label: string;
}

const nf = (n: number) => n.toLocaleString();

export const ABOUT_FACTS: AboutFact[] = [
  { value: nf(aboutStats.lines_of_code), label: "lines of code" },
  { value: nf(aboutStats.tracked_files), label: "files in the repo" },
  { value: String(aboutStats.languages.length), label: "languages" },
  { value: String(aboutStats.counts.services), label: "services running" },
  { value: String(aboutStats.counts.contract_event_types), label: "event types on the wire" },
  { value: String(aboutStats.counts.adrs), label: "architecture decisions written down" },
  { value: String(aboutStats.counts.test_files), label: "test files" },
  { value: String(aboutStats.counts.task_briefs), label: "task briefs" },
];

/**
 * Facts about the machine rather than the codebase.
 *
 * Deliberately concrete and deliberately checkable — each of these is a number from the
 * hardware, the pinned model, or a measurement taken on this door, not a marketing claim.
 * Anything I could not point at is not here.
 */
export const ABOUT_TECH_FACTS: string[] = [
  "The bell is wired to a microcontroller, not to the computer. Press it and the light and " +
    "chime fire in under a tenth of a second — that path does not touch the network, the " +
    "operating system, or anything that can be slow.",
  "Faces are compared as 512 numbers. A photo becomes a vector, the vector is compared to " +
    "the ones people enrolled, and the photo is thrown away. The comparison takes about 14 " +
    "milliseconds on the AI chip in this door.",
  "There are two cameras because one lens cannot do both jobs: a wide one to frame whoever " +
    "is at the door, a narrower one to put enough pixels on a face to recognise it.",
  "Nothing about an unrecognised face is written down — not a template, not a hash, not a " +
    "'seen before' counter. There is no list for you to be on.",
  "The door keeps working when the house network does not. Recognition, the bell, the " +
    "screens and the guestbook are all local; the rest is a bonus that can be missing.",
];

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
    heading: "If the door speaks your name",
    body:
      "A screen at arm's length shows your name to you. A speaker tells everyone in the " +
      "hallway. So saying your name out loud is a separate choice from enrolling: it is off " +
      "unless you opt in specifically, it stays quiet overnight, and it will not repeat " +
      "itself at someone standing in their own doorway.",
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

export function AboutDoorboard({
  className = "",
  showFacts = false,
}: {
  className?: string;
  /**
   * Render the numbers and the hardware facts as well as the prose.
   *
   * On by the caller rather than sniffed from `className`: the doorpad screen is a
   * scrolling column on a 7" panel where the extra material would bury the part a visitor
   * actually needs (what is recorded, and how to erase it).
   */
  showFacts?: boolean;
}) {
  return (
    <div className={`about-doorboard ${className}`} data-testid="about-doorboard">
      {ABOUT_SECTIONS.map((section) => (
        <section key={section.heading} className="about-doorboard__section">
          <h3 className="about-doorboard__heading">{section.heading}</h3>
          <p className="about-doorboard__body">{section.body}</p>
        </section>
      ))}

      {showFacts ? (
        <>
          <section className="about-doorboard__section about-doorboard__section--facts">
            <h3 className="about-doorboard__heading">By the numbers</h3>
            <dl className="about-facts" data-testid="about-facts">
              {ABOUT_FACTS.map((fact) => (
                <div className="about-facts__item" key={fact.label}>
                  <dt className="about-facts__value">{fact.value}</dt>
                  <dd className="about-facts__label">{fact.label}</dd>
                </div>
              ))}
            </dl>
            <p className="about-facts__asof">
              Counted from the source on {aboutStats.generated_at} ·{" "}
              {aboutStats.languages.map((lang) => lang.name).join(" · ")}
            </p>
          </section>

          <section className="about-doorboard__section">
            <h3 className="about-doorboard__heading">How it actually works</h3>
            <ul className="about-tech-facts" data-testid="about-tech-facts">
              {ABOUT_TECH_FACTS.map((fact) => (
                <li key={fact.slice(0, 24)} className="about-tech-facts__item">
                  {fact}
                </li>
              ))}
            </ul>
          </section>
        </>
      ) : null}
    </div>
  );
}
