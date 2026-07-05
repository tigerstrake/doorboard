import React, { useEffect, useState } from "react";

export interface TileProps {
  title: string;
  asOf?: string | null;
  children?: React.ReactNode;
  className?: string;
}

export function Tile({ title, asOf, children, className = "" }: TileProps) {
  const [stalenessText, setStalenessText] = useState<string>("");

  useEffect(() => {
    if (!asOf) {
      setStalenessText("");
      return;
    }

    const updateStaleness = () => {
      const date = new Date(asOf);
      const now = new Date();
      const diffMs = now.getTime() - date.getTime();
      const diffMins = Math.floor(diffMs / 60000);

      if (isNaN(diffMins)) {
        setStalenessText("");
        return;
      }

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
  }, [asOf]);

  return (
    <div className={`db-tile ${className}`} data-testid="tile">
      <div className="db-tile__header">
        <h3 className="db-tile__title">{title}</h3>
        {stalenessText && (
          <span className="db-tile__staleness" title={`Updated: ${asOf}`}>
            {stalenessText}
          </span>
        )}
      </div>
      <div className="db-tile__content">{children}</div>
    </div>
  );
}
