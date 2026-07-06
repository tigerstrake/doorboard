import React, { useEffect, useRef, useState } from "react";

export interface CrossfadeSwitchProps {
  /** Identifies which view is active; changing it triggers a crossfade. */
  activeKey: string;
  children: React.ReactNode;
  durationMs?: number;
  className?: string;
}

interface Layer {
  key: string;
  node: React.ReactNode;
  fadingOut: boolean;
}

// Keeps the outgoing view mounted (opacity fading to 0) while the incoming view
// fades in on top of it, so ambient<->visitor-mode swaps never show a bare/white
// frame and never hard-cut mid-animation. Safe to interrupt: retargeting activeKey
// again before a fade finishes just drops the stale layer and starts a fresh fade.
export function CrossfadeSwitch({
  activeKey,
  children,
  durationMs = 400,
  className = "",
}: CrossfadeSwitchProps) {
  const [layers, setLayers] = useState<Layer[]>([{ key: activeKey, node: children, fadingOut: false }]);
  const timeoutsRef = useRef<ReturnType<typeof setTimeout>[]>([]);

  useEffect(() => {
    setLayers((prev) => {
      const withoutStaleFades = prev.filter((layer) => layer.key === activeKey);
      if (withoutStaleFades.length > 0) {
        // Same key re-rendering with new children — update content in place, no fade.
        return [{ key: activeKey, node: children, fadingOut: false }];
      }
      const fadingOutPrev = prev.map((layer) => ({ ...layer, fadingOut: true }));
      return [...fadingOutPrev, { key: activeKey, node: children, fadingOut: false }];
    });
  }, [activeKey, children]);

  useEffect(() => {
    timeoutsRef.current.forEach(clearTimeout);
    timeoutsRef.current = [];

    const hasFadingOut = layers.some((layer) => layer.fadingOut);
    if (!hasFadingOut) return;

    const timeout = setTimeout(() => {
      setLayers((prev) => prev.filter((layer) => !layer.fadingOut));
    }, durationMs);
    timeoutsRef.current.push(timeout);

    return () => clearTimeout(timeout);
  }, [layers, durationMs]);

  return (
    <div className={`db-crossfade ${className}`} data-testid="crossfade-switch">
      {layers.map((layer) => (
        <div
          key={layer.key}
          className={`db-crossfade__layer ${layer.fadingOut ? "db-crossfade__layer--out" : "db-crossfade__layer--in"}`}
          style={{ transitionDuration: `${durationMs}ms` }}
        >
          {layer.node}
        </div>
      ))}
    </div>
  );
}
