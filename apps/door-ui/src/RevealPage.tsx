import React, { useEffect, useState } from "react";
import { VisitorCollageContent } from "./wallboardChannels";
import type { VisitorCollage } from "./wallboardChannels";

// The owner-only "class year in review" reveal. This page is intentionally
// SECRET: it is never linked from the doorpad or wallboard UI and is only
// reachable by typing `/reveal#<owner-token>`. The visitor collage collects
// silently all year and is only shown here, on-demand (e.g. the last day of
// school), as a surprise. It must never appear on the public 27" wallboard.
const API_BASE = import.meta.env.VITE_DOOR_API_BASE_URL ?? "http://127.0.0.1:8000";

type RevealStatus = "loading" | "revealed" | "denied";

/**
 * The owner token travels in the URL *hash* fragment (`/reveal#<token>`), which
 * — unlike a query string — is never sent to the server, so it stays out of
 * access logs, the `Referer` header, and shared-link previews.
 */
function readTokenFromHash(): string {
  const raw = window.location.hash.startsWith("#")
    ? window.location.hash.slice(1)
    : window.location.hash;
  try {
    return decodeURIComponent(raw).trim();
  } catch {
    return raw.trim();
  }
}

function formatRevealDate(value: string | null | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return null;
  return date.toLocaleDateString([], { month: "long", day: "numeric", year: "numeric" });
}

export function RevealPage() {
  const [status, setStatus] = useState<RevealStatus>("loading");
  const [collage, setCollage] = useState<VisitorCollage | null>(null);

  useEffect(() => {
    const token = readTokenFromHash();
    if (!token) {
      setStatus("denied");
      return undefined;
    }
    const controller = new AbortController();
    fetch(`${API_BASE}/admin/visitor-collage`, {
      signal: controller.signal,
      headers: { Authorization: `Bearer ${token}` },
    })
      .then(async (response) => {
        // Any non-2xx (401 invalid token, 503 unconfigured, upstream error)
        // collapses to the neutral "denied" state — no data, no hint.
        if (!response.ok) throw new Error(`reveal unavailable: ${response.status}`);
        return (await response.json()) as VisitorCollage;
      })
      .then((data) => {
        setCollage(data);
        setStatus("revealed");
      })
      .catch(() => {
        if (!controller.signal.aborted) setStatus("denied");
      });
    return () => controller.abort();
  }, []);

  if (status === "loading") {
    // Neutral placeholder while the fetch resolves — reveals nothing either way.
    return <main className="reveal-page reveal-page--loading db-app-theme" aria-busy="true" />;
  }

  if (status === "denied") {
    // Deliberately information-free: a visitor who stumbles onto /reveal (or
    // presents a bad token) must not learn that anything exists here.
    return (
      <main className="reveal-page reveal-page--empty db-app-theme">
        <p className="reveal-empty-note">Nothing to see here.</p>
      </main>
    );
  }

  const stats = collage?.stats ?? null;
  const firstVisit = formatRevealDate(stats?.first_checkin_at);
  const lastVisit = formatRevealDate(stats?.most_recent_checkin_at);

  return (
    <main className="reveal-page reveal-page--revealed db-app-theme">
      <header className="reveal-hero">
        <p className="reveal-eyebrow">The last day of school</p>
        <h1 className="reveal-title">Everyone who stopped by this year</h1>
        <p className="reveal-subtitle">A surprise, saved up all year long.</p>
      </header>

      {stats && (
        <section className="reveal-stats" aria-label="Year in review">
          <div className="reveal-stat">
            <strong>{stats.total_checkins}</strong>
            <span>total visits</span>
          </div>
          <div className="reveal-stat">
            <strong>{stats.unique_visitors}</strong>
            <span>visitor{stats.unique_visitors === 1 ? "" : "s"}</span>
          </div>
          <div className="reveal-stat">
            <strong>{stats.checkins_this_year}</strong>
            <span>this year</span>
          </div>
          {stats.most_frequent && (
            <div className="reveal-stat">
              <strong>{stats.most_frequent.label?.trim() || "Guest"}</strong>
              <span>most frequent ({stats.most_frequent.count})</span>
            </div>
          )}
          {firstVisit && (
            <div className="reveal-stat">
              <strong>{firstVisit}</strong>
              <span>first visit</span>
            </div>
          )}
          {lastVisit && (
            <div className="reveal-stat">
              <strong>{lastVisit}</strong>
              <span>most recent</span>
            </div>
          )}
        </section>
      )}

      <section className="reveal-collage" aria-label="Visitor check-in photos">
        {/* Reuse the collage photo grid; the reveal renders its own big stats
            above, so suppress the compact chip panel here. */}
        <VisitorCollageContent
          collage={collage}
          maxPhotos={60}
          variant="focus"
          showStats={false}
        />
      </section>
    </main>
  );
}
