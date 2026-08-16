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
  /**
   * Print the URL as text under the code. On by default, because the visitor QR is
   * a plain link worth being able to type. Turn it off for an enrollment invite:
   * the URL carries a single-use secret, it is far too long to read off a 7" panel,
   * and the code above it is the only part anyone needs.
   */
  showUrl?: boolean;
}

export function QRPlaceholder({
  url,
  text = "Scan to visit on your phone",
  className = "",
  alt = "Visitor link QR code",
  size = 320,
  showUrl = true,
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
      {showUrl && (
        <span className="db-qr-placeholder__url" data-testid="qr-placeholder-url">
          {url}
        </span>
      )}
    </div>
  );
}
