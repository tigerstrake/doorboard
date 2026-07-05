import React, { useState } from "react";
import { Tile } from "../Tile";
import { StatusBadge } from "../StatusBadge";
import { BigButton } from "../BigButton";
import { GreetingBanner } from "../GreetingBanner";
import { CountdownAutoReset } from "../CountdownAutoReset";
import { QRPlaceholder } from "../QRPlaceholder";

export const Tiles = () => (
  <div style={{ display: "flex", gap: "16px", padding: "16px", flexWrap: "wrap", background: "#0b0f19" }}>
    <Tile title="Presence">
      <div style={{ display: "flex", gap: "8px" }}>
        <StatusBadge label="available" />
        <StatusBadge label="busy" />
      </div>
    </Tile>
    <Tile title="Stale Data Tile" asOf={new Date(Date.now() - 3600000).toISOString()}>
      <div>This tile is 1 hour stale.</div>
    </Tile>
    <Tile title="Fresh Data Tile" asOf={new Date().toISOString()}>
      <div>This tile was just updated.</div>
    </Tile>
  </div>
);

export const StatusBadges = () => (
  <div style={{ display: "flex", flexDirection: "column", gap: "8px", padding: "16px", background: "#0b0f19" }}>
    <StatusBadge label="available" />
    <StatusBadge label="busy" />
    <StatusBadge label="do_not_disturb" />
    <StatusBadge label="sleeping" />
    <StatusBadge label="at_class" />
    <StatusBadge label="at_library" />
    <StatusBadge label="away" />
    <StatusBadge label="unknown" />
  </div>
);

export const BigButtons = () => (
  <div style={{ display: "flex", gap: "16px", padding: "16px", background: "#0b0f19" }}>
    <BigButton icon={<span>🔔</span>} onClick={() => alert("Ring pressed")}>
      Ring Doorbell
    </BigButton>
    <BigButton variant="primary" icon={<span>💬</span>} onClick={() => alert("Message pressed")}>
      Leave Message
    </BigButton>
    <BigButton disabled icon={<span>🔒</span>}>
      Locked Button
    </BigButton>
  </div>
);

export const Banners = () => (
  <div style={{ display: "flex", flexDirection: "column", gap: "16px", padding: "16px", background: "#0b0f19" }}>
    <GreetingBanner title="Welcome to Room 304" subtitle="Ambient Mode active" />
    <GreetingBanner title="Welcome back, Taylor" subtitle="Owner recognized" profileId="owner" />
    <GreetingBanner title="Hey, Alex!" subtitle="Roommate recognized" profileId="roommate" />
    <GreetingBanner title="Hello, Visitor" subtitle="Visitor Mode active" profileId="visitor" />
  </div>
);

export const AutoReset = () => {
  const [resetCount, setResetCount] = useState(0);
  return (
    <div style={{ padding: "16px", background: "#0b0f19", color: "white" }}>
      <p>Reset count: {resetCount}</p>
      <CountdownAutoReset timeoutMs={5000} onReset={() => setResetCount(c => c + 1)}>
        <div style={{ padding: "32px", border: "1px dashed #ffffff33", borderRadius: "8px" }}>
          Interact with this page to reset the 5-second timer.
        </div>
      </CountdownAutoReset>
    </div>
  );
};

export const QRDisplay = () => (
  <div style={{ padding: "16px", background: "#0b0f19" }}>
    <QRPlaceholder url="http://door.local/visitor?token=123" />
  </div>
);
