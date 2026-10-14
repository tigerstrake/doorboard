// A video source for VisionSystem other than getUserMedia. On the door the camera
// belongs to door-media, so the game reads the visitor stream it already publishes
// (ADR-0049) instead of opening a camera device of its own.

export interface OpenedCamera {
  stream: MediaStream;
  /** Frees whatever opening the stream took (e.g. the WebRTC session). Idempotent. */
  release: () => void;
}

export interface CameraSource {
  /** How error messages name this source ("The door camera"). */
  readonly label: string;
  /**
   * Resolve a live stream. `onLost` fires once if the stream goes away later: a
   * remote track stopped by its owner does not reliably fire 'ended' on our side.
   */
  open(onLost: () => void): Promise<OpenedCamera>;
}
