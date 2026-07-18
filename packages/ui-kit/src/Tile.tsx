import React, { useEffect, useState } from "react";

export interface TileProps {
  title: string;
  asOf?: string | null;
  children?: React.ReactNode;
  className?: string;
  staleAfterMs?: number;
}

export function Tile({
  title,
  asOf,
  children,
  className = "",
  staleAfterMs = 15 * 60 * 1000,
}: TileProps) {
  const [stalenessText, setStalenessText] = useState<string>("");
  const [isStale, setIsStale] = useState(false);

  useEffect(() => {
    if (!asOf) {
      setStalenessText("");
      setIsStale(false);
      return;
    }

    const updateStaleness = () => {
      const date = new Date(asOf);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffMins = Math.floor(diffMs / 60000);

      if (isNaN(diffMins)) {
        setStalenessText("");
        setIsStale(false);
        return;
      }
      setIsStale(diffMs > staleAfterMs);

      if (diffMins < 1) {
        setStalenessText("Just now");
      } else if (diffMins < 60) {
        setStalenessText(`${diffMins}m ago`);
      } else {
        const diffHours = Math.floor(diffMins / 60);
        setStalenessText(`${diffHours}h ago`);
      }
    };

    updateStaleness();
    const interval = setInterval(updateStaleness, 30000);
    return () => clearInterval(interval);
  }, [asOf, staleAfterMs]);

  return (
    <div
      className={`db-tile ${isStale ? "db-tile--stale" : ""} ${className}`}
      data-testid="tile"
      data-stale={isStale ? "true" : "false"}
    >
      <div className="db-tile__header">
        <h3 className="db-tile__title">{title}</h3>
        {stalenessText && (
          <span className="db-tile__staleness" title={`Updated: ${asOf}`}>
            {isStale ? `Stale · ${stalenessText}` : stalenessText}
          </span>
        )}
      </div>
      <div className="db-tile__content">{children}</div>
    </div>
  );
}
