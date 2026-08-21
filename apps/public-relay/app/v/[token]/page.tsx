import VisitorFlow from "./VisitorFlow";

/**
 * The visitor page, reached from the wallboard QR (ADR-0017).
 *
 * This exists because the LAN version at `http://door.local/visitor` cannot load
 * on a phone that is on cellular — which is every stranger at the door. door-ui
 * keeps its copy for the on-wifi and internet-down fallback; this one talks to the
 * relay instead of to door-api.
 */
export const dynamic = "force-dynamic";

export default async function VisitorPage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = await params;
  return <VisitorFlow token={token} />;
}
