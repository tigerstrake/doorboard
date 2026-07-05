import React from "react";

export interface GreetingBannerProps {
  title: string;
  subtitle?: string;
  profileId?: string | null;
  className?: string;
}

export function GreetingBanner({
  title,
  subtitle,
  profileId,
  className = "",
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
      className={`db-greeting-banner ${variantClass} ${className}`}
      data-testid="greeting-banner"
    >
      <h1 className="db-greeting-banner__title">{title}</h1>
      {subtitle && <p className="db-greeting-banner__subtitle">{subtitle}</p>}
    </div>
  );
}
