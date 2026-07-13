import React, { useEffect, useState } from "react";
import QRCode from "qrcode";

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
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setImageUrl(null);
    QRCode.toDataURL(url, {
      width: 320,
      margin: 1,
      errorCorrectionLevel: "M",
      color: { dark: "#111111", light: "#ffffff" },
    })
      .then((generated) => {
        if (!cancelled) setImageUrl(generated);
      })
      .catch(() => {
        if (!cancelled) setImageUrl(null);
      });
    return () => {
      cancelled = true;
    };
  }, [url]);

  return (
    <div className={`db-qr-placeholder ${className}`} data-testid="qr-placeholder">
      <div className="db-qr-placeholder__graphic">
        {imageUrl ? <img src={imageUrl} alt="Visitor link QR code" /> : null}
      </div>
      <p className="db-qr-placeholder__text">{text}</p>
      <span className="db-qr-placeholder__url" data-testid="qr-placeholder-url">
        {url}
      </span>
    </div>
  );
}
