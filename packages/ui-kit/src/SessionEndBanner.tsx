import React from "react";

export interface SessionEndBannerProps {
  title?: string;
  subtitle?: string;
  className?: string;
}

export function SessionEndBanner({
  title = "Thanks for stopping by!",
  subtitle = "Have a great day.",
  className = "",
}: SessionEndBannerProps) {
  return (
    <div className={`db-session-end-banner ${className}`} data-testid="session-end-banner">
      <span className="db-session-end-banner__icon" aria-hidden="true">
        👋
      </span>
      <h1 className="db-session-end-banner__title">{title}</h1>
      <p className="db-session-end-banner__subtitle">{subtitle}</p>
    </div>
  );
}
