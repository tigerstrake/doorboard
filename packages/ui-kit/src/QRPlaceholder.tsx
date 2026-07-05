import React from "react";

export interface QRPlaceholderProps {
  url: string;
  text?: string;
  className?: string;
}

export function QRPlaceholder({
  url,
  text = "Scan to visit on your phone",
  className = "",
}: QRPlaceholderProps) {
  return (
    <div className={`db-qr-placeholder ${className}`} data-testid="qr-placeholder">
      <div className="db-qr-placeholder__graphic">
        {/* Simple mock QR pattern */}
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
        <div />
      </div>
      <p className="db-qr-placeholder__text">{text}</p>
      <span style={{ fontSize: "0.75rem", color: "var(--db-text-muted)", wordBreak: "break-all" }}>
        {url}
      </span>
    </div>
  );
}
