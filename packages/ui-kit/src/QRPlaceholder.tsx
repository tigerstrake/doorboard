import React, { useEffect, useState } from "react";
import QRCode from "qrcode";

export interface QRPlaceholderProps {
  url: string;
  text?: string;
  className?: string;
  /** Accessible name for the code. Override when it is not a visitor link. */
  alt?: string;
  /** Pixel width of the generated code. Larger for codes scanned across a room. */
  size?: number;
}

export function QRPlaceholder({
  url,
  text = "Scan to visit on your phone",
  className = "",
  alt = "Visitor link QR code",
  size = 320,
}: QRPlaceholderProps) {
  const [imageUrl, setImageUrl] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setImageUrl(null);
    QRCode.toDataURL(url, {
      width: size,
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
  }, [url, size]);

  return (
    <div className={`db-qr-placeholder ${className}`} data-testid="qr-placeholder">
      <div className="db-qr-placeholder__graphic">
        {imageUrl ? <img src={imageUrl} alt={alt} /> : null}
      </div>
      <p className="db-qr-placeholder__text">{text}</p>
      <span className="db-qr-placeholder__url" data-testid="qr-placeholder-url">
        {url}
      </span>
    </div>
  );
}
