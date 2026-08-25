import VisitorFlow from "./VisitorFlow";

/**
 * The visitor page, reached from the wallboard QR (ADR-0017). On Cloudflare this exports as
 * ONE static shell (ADR-0043 §1): visitor tokens aren't known at build, so
 * `generateStaticParams` emits a single placeholder and `public/_redirects` serves it for
 * every `/v/<token>`. The client reads the real token from the path, so the wallboard-built
 * URL `/v/<token>` is unchanged. door-ui keeps its own copy for the on-wifi / internet-down
 * fallback; this one talks to the relay.
 */
export function generateStaticParams(): Array<{ token: string }> {
  return [{ token: "_" }];
}

export default function VisitorPage() {
  return <VisitorFlow />;
}
