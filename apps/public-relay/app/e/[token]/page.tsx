import EnrollFlow from "./EnrollFlow";

/**
 * The invite page.
 *
 * Rendered as a thin server shell around a client component on purpose: the
 * invite secret is in the URL path and the key fingerprint is in the fragment,
 * and the fragment is never sent to a server. All verification and sealing
 * therefore has to happen in the browser (ADR-0016 §3, E-10).
 */
export const dynamic = "force-dynamic";

export default async function InvitePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <EnrollFlow token={token} />;
}
