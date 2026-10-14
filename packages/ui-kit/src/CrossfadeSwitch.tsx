import React, { useEffect, useState } from "react";

export interface CrossfadeSwitchProps {
  /** Identifies which view is active; changing it triggers a crossfade. */
  activeKey: string;
  children: React.ReactNode;
  durationMs?: number;
  className?: string;
  /**
   * How the swap looks.
   *
   * `fade` is the original opacity-only crossfade. `zoom` adds a scale on both layers so
   * opening a channel reads as moving *into* it — the grid recedes as the larger view comes
   * forward — which is what "from the ambient grid to the larger views" asks for. Both are
   * transform/opacity only: those are the two things a compositor can animate without
   * touching layout, which matters on a Pi driving a 27" panel.
   */
  variant?: "fade" | "zoom";
}

interface Layer {
  key: string;
  node: React.ReactNode;
  fadingOut: boolean;
}

// Keeps the outgoing view mounted (opacity fading to 0) while the incoming view
// fades in on top of it, so ambient<->visitor-mode swaps never show a bare/white
// frame and never hard-cut mid-animation. The outgoing layer's node is frozen as it
// was at the swap: a parent re-render mid-fade (the 1 s clock tick, a token fetch)
// updates only the active layer and neither drops the fading one nor restarts its
// clock. Safe to interrupt: retargeting activeKey again before a fade finishes
// fades the interrupted layer out too and starts a fresh fade.
export function CrossfadeSwitch({
  activeKey,
  children,
  durationMs = 400,
  className = "",
  variant = "fade",
}: CrossfadeSwitchProps) {
  const [layers, setLayers] = useState<Layer[]>([{ key: activeKey, node: children, fadingOut: false }]);

  useEffect(() => {
    setLayers((prev) => {
      const active = prev.find((layer) => layer.key === activeKey);
      if (active && !active.fadingOut) {
        // Same key re-rendering with new children: update its content in place and
        // leave any fade in flight exactly as it is.
        return prev.map((layer) => (layer === active ? { ...layer, node: children } : layer));
      }
      // A new key (or one coming back mid-fade): everything else fades out, frozen.
      return [
        ...prev
          .filter((layer) => layer.key !== activeKey)
          .map((layer) => (layer.fadingOut ? layer : { ...layer, fadingOut: true })),
        { key: activeKey, node: children, fadingOut: false },
      ];
    });
  }, [activeKey, children]);

  useEffect(() => {
    // One removal timer per swap. Keyed on the swap, not on `layers`, so a re-render
    // during the fade cannot re-arm it and keep the old view around past durationMs.
    const timeout = setTimeout(() => {
      setLayers((prev) => (prev.some((layer) => layer.fadingOut) ? prev.filter((layer) => !layer.fadingOut) : prev));
    }, durationMs);
    return () => clearTimeout(timeout);
  }, [activeKey, durationMs]);

  return (
    <div
      className={`db-crossfade db-crossfade--${variant} ${className}`}
      data-testid="crossfade-switch"
      data-variant={variant}
    >
      {layers.map((layer) => (
        <div
          key={layer.key}
          className={`db-crossfade__layer ${layer.fadingOut ? "db-crossfade__layer--out" : "db-crossfade__layer--in"}`}
          // Both the transition (fade-out) and the keyframe (zoom-in) read the duration,
          // so it is set once here rather than duplicated in CSS and drifting.
          style={
            {
              transitionDuration: `${durationMs}ms`,
              "--db-crossfade-duration": `${durationMs}ms`,
            } as React.CSSProperties
          }
        >
          {layer.node}
        </div>
      ))}
    </div>
  );
}
