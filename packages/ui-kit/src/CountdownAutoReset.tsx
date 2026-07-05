import React, { useEffect, useState, useRef } from "react";

export interface CountdownAutoResetProps {
  onReset: () => void;
  timeoutMs?: number; // default: 30000 (30s)
  children?: React.ReactNode;
  showProgress?: boolean;
  className?: string;
}

export function CountdownAutoReset({
  onReset,
  timeoutMs = 30000,
  children,
  showProgress = true,
  className = "",
}: CountdownAutoResetProps) {
  const [timeLeftMs, setTimeLeftMs] = useState<number>(timeoutMs);
  const onResetRef = useRef(onReset);
  onResetRef.current = onReset;

  useEffect(() => {
    setTimeLeftMs(timeoutMs);

    let lastActivity = Date.now();
    const handleActivity = () => {
      lastActivity = Date.now();
      setTimeLeftMs(timeoutMs);
    };

    const events = ["mousedown", "mousemove", "keypress", "touchstart", "scroll"];
    events.forEach((event) => {
      window.addEventListener(event, handleActivity);
    });

    const interval = setInterval(() => {
      const elapsed = Date.now() - lastActivity;
      const remaining = Math.max(0, timeoutMs - elapsed);
      setTimeLeftMs(remaining);

      if (remaining <= 0) {
        clearInterval(interval);
        onResetRef.current();
      }
    }, 100);

    return () => {
      events.forEach((event) => {
        window.removeEventListener(event, handleActivity);
      });
      clearInterval(interval);
    };
  }, [timeoutMs]);

  const percentage = (timeLeftMs / timeoutMs) * 100;

  return (
    <div className={`db-auto-reset-wrapper ${className}`} style={{ position: "relative" }}>
      {children}
      {showProgress && timeLeftMs < timeoutMs && (
        <div
          className="db-auto-reset-bar"
          style={{ width: `${percentage}%` }}
          data-testid="auto-reset-progress-bar"
        />
      )}
    </div>
  );
}
