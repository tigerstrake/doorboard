import EnrollFlow from "./EnrollFlow";

/**
 * The invite page.
 *
 * Rendered as a thin server shell around a client component on purpose: the
 * invite secret and the key fingerprint both travel in the URL fragment
 * (ADR-0043 §2, ADR-0016 §3), which is never sent to a server. The path segment
 * carries only the invite id. All verification and sealing therefore has to
 * happen in the browser (E-10).
 */
export const dynamic = "force-dynamic";

export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token: inviteId } = await params;
  return <EnrollFlow inviteId={inviteId} />;
}
