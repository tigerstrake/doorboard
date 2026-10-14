import { useEffect, useRef, type RefObject } from "react";
import {
  BODY_SURFERS_CLEAR_TIMEOUT_MS,
  BODY_SURFERS_READY_TIMEOUT_MS,
  gameMessageFrom,
  type BodySurfersExitReason,
  type GameToDoor,
} from "../bodySurfers";

/**
 * Listen to one frame's messages: only its own window, at its exact origin. Anything
 * else posting into the wallboard (another frame, an extension) is ignored.
 */
function useFrameMessages(
  frameRef: RefObject<HTMLIFrameElement | null>,
  origin: string,
  onMessage: (message: GameToDoor) => void,
) {
  const handlerRef = useRef(onMessage);
  useEffect(() => {
    handlerRef.current = onMessage;
  }, [onMessage]);

  useEffect(() => {
    const listener = (event: MessageEvent) => {
      if (event.origin !== origin || event.source !== frameRef.current?.contentWindow) return;
      const message = gameMessageFrom(event.data);
      if (message) handlerRef.current(message);
    };
    window.addEventListener("message", listener);
    return () => window.removeEventListener("message", listener);
  }, [frameRef, origin]);
}

// allow-same-origin lets the game keep its leaderboard in *its own* origin's storage;
// it is cross-site to the wallboard, so the frame still cannot reach this page. No
// popups, forms, top navigation or autoplay: nothing on the wallboard can be clicked,
// and a hallway game stays silent.
const SANDBOX = "allow-scripts allow-same-origin";

interface BodySurfersFrameProps {
  src: string;
  origin: string;
  onExit: (reason: BodySurfersExitReason) => void;
}

/** The running game, full-screen over the wallboard. Unmounting it ends the game. */
export function BodySurfersFrame({ src, origin, onExit }: BodySurfersFrameProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const readyRef = useRef(false);
  const onExitRef = useRef(onExit);
  useEffect(() => {
    onExitRef.current = onExit;
  }, [onExit]);

  useFrameMessages(frameRef, origin, (message) => {
    if (message.type === "ready") readyRef.current = true;
    else if (message.type === "exit") onExitRef.current(message.reason);
  });

  useEffect(() => {
    readyRef.current = false;
    const timer = window.setTimeout(() => {
      if (!readyRef.current) onExitRef.current("error");
    }, BODY_SURFERS_READY_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [src]);

  return (
    <div className="wallboard-game" data-testid="body-surfers-frame">
      <iframe ref={frameRef} src={src} title="Body Surfers" sandbox={SANDBOX} referrerPolicy="no-referrer" />
    </div>
  );
}

interface BodySurfersLeaderboardClearProps {
  src: string;
  origin: string;
  onDone: (cleared: boolean) => void;
}

/** A hidden frame that wipes the game's leaderboard (owner request), then goes away. */
export function BodySurfersLeaderboardClear({ src, origin, onDone }: BodySurfersLeaderboardClearProps) {
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  useFrameMessages(frameRef, origin, (message) => {
    if (message.type === "leaderboard-cleared") onDoneRef.current(true);
  });

  useEffect(() => {
    const timer = window.setTimeout(() => onDoneRef.current(false), BODY_SURFERS_CLEAR_TIMEOUT_MS);
    return () => window.clearTimeout(timer);
  }, [src]);

  return (
    <iframe
      ref={frameRef}
      src={src}
      title="Body Surfers leaderboard clear"
      sandbox={SANDBOX}
      referrerPolicy="no-referrer"
      hidden
      data-testid="body-surfers-clear"
    />
  );
}
