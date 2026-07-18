import React, { useEffect, useRef, useState } from "react";
import {
  createWhepPlayer,
  getMediaClientStats,
  type StreamHealth,
  type WhepPlayer,
  type WhepPlayerSnapshot,
} from "@doorboard/media-client";

export interface LiveVideoPreviewProps {
  title?: string;
  metadataUrl?: string;
  streamName?: string;
  className?: string;
  showStats?: boolean;
}

const DEFAULT_METADATA_URL = "/door-media/streams";
const DEFAULT_STREAM_NAME = "visitor";

const INITIAL_SNAPSHOT: WhepPlayerSnapshot = {
  status: "connecting",
  stream: null,
  streamName: DEFAULT_STREAM_NAME,
  lastError: null,
  connectedAtMonotonicMs: null,
};

export function LiveVideoPreview({
  title = "Live View",
  metadataUrl = DEFAULT_METADATA_URL,
  streamName = DEFAULT_STREAM_NAME,
  className = "",
  showStats = false,
}: LiveVideoPreviewProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const playerRef = useRef<WhepPlayer | null>(null);
  const [snapshot, setSnapshot] = useState<WhepPlayerSnapshot>({
    ...INITIAL_SNAPSHOT,
    streamName,
  });

  useEffect(() => {
    const player = createWhepPlayer({
      metadataUrl,
      streamName,
      onChange: setSnapshot,
    });
    playerRef.current = player;
    player.start();
    return () => {
      player.stop();
      playerRef.current = null;
    };
  }, [metadataUrl, streamName]);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) {
      return;
    }
    if (video.srcObject !== snapshot.stream) {
      video.srcObject = snapshot.stream;
    }
  }, [snapshot.stream, snapshot.status]);

  const statusLabel = statusText(snapshot.status);
  const stats = getMediaClientStats();

  return (
    <section className={`db-live-video ${className}`} data-testid="live-video">
      <div className="db-live-video__header">
        <h3>{title}</h3>
        <span
          className={`db-live-video__status db-live-video__status--${snapshot.status}`}
          data-testid="live-video-state"
        >
          {statusLabel}
        </span>
      </div>
      <div className="db-live-video__frame">
        {snapshot.stream && snapshot.status === "connected" ? (
          <video
            ref={videoRef}
            className="db-live-video__video"
            autoPlay
            muted
            playsInline
            data-testid="live-video-element"
          />
        ) : (
          <div className="db-live-video__empty" role="status">
            <strong>{statusLabel}</strong>
            {snapshot.lastError && (
              <span className="db-live-video__detail">{snapshot.lastError}</span>
            )}
          </div>
        )}
      </div>
      {showStats && (
        <dl className="db-live-video__stats" aria-label="Live video diagnostics">
          <div>
            <dt>PeerConnections</dt>
            <dd>{stats.activePeerConnections}</dd>
          </div>
          <div>
            <dt>Created</dt>
            <dd>{stats.createdPeerConnections}</dd>
          </div>
          <div>
            <dt>Closed</dt>
            <dd>{stats.closedPeerConnections}</dd>
          </div>
        </dl>
      )}
    </section>
  );
}

function statusText(status: StreamHealth): string {
  if (status === "connected") {
    return "Connected";
  }
  if (status === "connecting") {
    return "Connecting";
  }
  return "Video unavailable";
}
