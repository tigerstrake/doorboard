import React from "react";

export interface GreetingBannerProps {
  title: string;
  subtitle?: string;
  profileId?: string | null;
  className?: string;
  /** Plays a brief celebratory effect — reserved for enrolled-identity greetings. */
  celebratory?: boolean;
  /** True for one render pass right after an identity arrives mid-session (late recognition). */
  justUpgraded?: boolean;
}

export function GreetingBanner({
  title,
  subtitle,
  profileId,
  className = "",
  celebratory = false,
  justUpgraded = false,
}: GreetingBannerProps) {
  let variantClass = "";
  if (profileId) {
    const normId = profileId.toLowerCase();
    if (normId.includes("owner")) {
      variantClass = "db-greeting-banner--owner";
    } else if (normId.includes("roommate")) {
      variantClass = "db-greeting-banner--roommate";
    } else {
      variantClass = "db-greeting-banner--visitor";
    }
  }

  return (
    <div
      className={`db-greeting-banner ${variantClass} ${justUpgraded ? "db-greeting-banner--upgraded" : ""} ${className}`}
      data-testid="greeting-banner"
    >
      <h1 className="db-greeting-banner__title">{title}</h1>
      {subtitle && <p className="db-greeting-banner__subtitle">{subtitle}</p>}
      {celebratory && (
        <span className="db-greeting-banner__sparkles" aria-hidden="true" data-testid="greeting-banner-sparkles">
          <span />
          <span />
          <span />
          <span />
          <span />
        </span>
      )}
    </div>
  );
}
