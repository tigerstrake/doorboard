import React from "react";

export type VideoMessageState =
  | "VIDEO_MESSAGE_OFFERED"
  | "VIDEO_MESSAGE_RECORDING"
  | "VIDEO_MESSAGE_REVIEW"
  | "VIDEO_MESSAGE_SAVED";

export interface VideoMessageStatusProps {
  state: VideoMessageState;
  className?: string;
}

// Privacy: this component never renders a camera feed or frame preview — only a
// text/icon status. The recording itself is visitor-initiated and lives on door-media.
const copy: Record<VideoMessageState, { icon: string; title: string; subtitle: string }> = {
  VIDEO_MESSAGE_OFFERED: {
    icon: "🎥",
    title: "Leave a video message?",
    subtitle: "Use the DoorPad below to record a short message.",
  },
  VIDEO_MESSAGE_RECORDING: {
    icon: "🔴",
    title: "Recording in progress",
    subtitle: "The visitor is recording a message on the DoorPad.",
  },
  VIDEO_MESSAGE_REVIEW: {
    icon: "👀",
    title: "Reviewing message",
    subtitle: "The visitor is reviewing their message before saving.",
  },
  VIDEO_MESSAGE_SAVED: {
    icon: "💾",
    title: "Message saved",
    subtitle: "Thanks — your message has been saved.",
  },
};

export function VideoMessageStatus({ state, className = "" }: VideoMessageStatusProps) {
  const { icon, title, subtitle } = copy[state];
  const isRecording = state === "VIDEO_MESSAGE_RECORDING";

  return (
    <div
      className={`db-video-message-status db-video-message-status--${state.toLowerCase()} ${className}`}
      data-testid="video-message-status"
      data-state={state}
    >
      <span
        className={`db-video-message-status__icon ${isRecording ? "db-video-message-status__icon--recording" : ""}`}
        aria-hidden="true"
      >
        {icon}
      </span>
      <div className="db-video-message-status__text">
        <h2 className="db-video-message-status__title">{title}</h2>
        <p className="db-video-message-status__subtitle">{subtitle}</p>
      </div>
    </div>
  );
}
