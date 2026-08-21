/**
 * Landing page. Reached by anyone who types the bare domain.
 *
 * Says what this is and nothing else: no enrollee list, no door detail, no way in
 * without an invite URL (ADR-0016 E-14).
 */
export default function Home() {
  return (
    <>
      <h1>Doorboard enrolment</h1>
      <p className="lede">
        This site helps someone set up a personalised greeting on a doorboard, using their phone.
      </p>

      <div className="card">
        <h2>You need an invitation</h2>
        <p>
          Enrolment only works from a single-use link, shown as a QR code on the doorboard itself.
          Ask the household admin to generate one for you.
        </p>
        <p style={{ marginBottom: 0 }}>
          If you have the link already, open it directly — it looks like <code>/e/…</code>.
        </p>
      </div>

      <div className="card">
        <h2>What this site can and cannot see</h2>
        <p>
          Your photos and your name are encrypted on your phone before they are sent, using a key
          only the door device can undo. This site stores the encrypted result for a few minutes and
          hands it to the door. It cannot read your photos or your name, and it holds no key that
          could.
        </p>
        <p style={{ marginBottom: 0 }}>
          Face templates live on the door device. They are never uploaded here.
        </p>
      </div>

      <p className="footnote">
        Prefer to keep everything off the internet? Enrol at the door instead — the doorboard has the
        same flow built in, using its own camera, and sends nothing anywhere.
      </p>
    </>
  );
}
