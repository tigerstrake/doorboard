import React from "react";

export interface GaugeProps {
  title: string;
  value: number | string;
  max?: number;
  unit?: string;
  percentage?: number; // Optional pre-calculated percentage (0-100)
  severity?: "normal" | "warning" | "critical";
}

export function Gauge({
  title,
  value,
  max,
  unit = "",
  percentage,
  severity = "normal",
}: GaugeProps) {
  let pct = 0;
  if (percentage !== undefined) {
    pct = percentage;
  } else if (max && typeof value === "number") {
    pct = (value / max) * 100;
  }
  
  pct = Math.max(0, Math.min(100, pct));
  
  const severityClass = `gauge-bar-fill ${severity}`;

  return (
    <div className="db-gauge">
      <div className="gauge-header">
        <span className="gauge-title">{title}</span>
        <span className="gauge-value">
          {value}
          {unit && <span className="gauge-unit"> {unit}</span>}
        </span>
      </div>
      <div className="gauge-track">
        <div className={severityClass} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}
