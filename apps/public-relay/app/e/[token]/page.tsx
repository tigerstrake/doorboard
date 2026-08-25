import EnrollFlow from "./EnrollFlow";

/**
 * The invite page (ADR-0016). On Cloudflare this exports as ONE static shell (ADR-0043 §1):
 * invite ids aren't known at build, so `generateStaticParams` emits a single placeholder and
 * `public/_redirects` serves it for every `/e/<id>`. The client reads the real id from the
 * path and the secret + key fingerprint from the URL fragment — so the door-built URL
 * `/e/<id>#s=<secret>&k=<fp>` is unchanged, and no server runtime is involved.
 */
export function generateStaticParams(): Array<{ token: string }> {
  return [{ token: "_" }];
}

export default function InvitePage() {
  return <EnrollFlow />;
}
