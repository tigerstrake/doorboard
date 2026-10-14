import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CAMERA_DISCONNECTED_MESSAGE, MODEL_DOWNLOAD_FAILED_MESSAGE, VisionSystem } from './VisionSystem';
import { FilesetResolver, PoseLandmarker } from '@mediapipe/tasks-vision';
import { VISION_CONFIG as C } from './config';
import { makeBody } from './synthetic';
import type { CameraSource } from './cameraSource';

const mediapipe = vi.hoisted(() => ({
  detectForVideo: vi.fn(),
  close: vi.fn(),
  setOptions: vi.fn(async () => {}),
}));

vi.mock('@mediapipe/tasks-vision', () => ({
  FilesetResolver: { forVisionTasks: vi.fn(async () => ({})) },
  PoseLandmarker: { createFromOptions: vi.fn(async () => mediapipe) },
}));

class FakeVideo extends EventTarget {
  readyState = 2;
  videoWidth = 1280;
  videoHeight = 720;
  currentTime = 0;
  srcObject: unknown = null;
  dataset = {};
  style = {};
  private nextId = 1;
  private callbacks = new Map<number, VideoFrameRequestCallback>();
  setAttribute(): void {}
  play = vi.fn(async () => {});
  remove = vi.fn();
  requestVideoFrameCallback = (callback: VideoFrameRequestCallback): number => {
    const id = this.nextId++;
    this.callbacks.set(id, callback);
    return id;
  };
  cancelVideoFrameCallback = (id: number): void => { this.callbacks.delete(id); };

  present(now: number, mediaTime: number, captureTime?: number): void {
    this.currentTime = mediaTime;
    const callbacks = [...this.callbacks.values()];
    this.callbacks.clear();
    for (const cb of callbacks) cb(now, {
      mediaTime, captureTime, presentedFrames: this.nextId, width: 1280, height: 720,
      presentationTime: now, expectedDisplayTime: now, processingDuration: 0,
    });
  }
}

class FakeTrack extends EventTarget {
  readyState: 'live' | 'ended' = 'live';
  label = 'Test camera';
  constructor(private readonly deviceId: string) {
    super();
  }
  stop = vi.fn(() => { this.readyState = 'ended'; });
  getSettings = () => ({ deviceId: this.deviceId });
  /** Simulate the camera being unplugged / taken by the OS. */
  unplug(): void {
    this.readyState = 'ended';
    this.dispatchEvent(new Event('ended'));
  }
}

class FakeMediaDevices extends EventTarget {
  devices = [{ kind: 'videoinput', deviceId: 'cam-1', label: 'Test camera' }];
  tracks: FakeTrack[] = [];
  getUserMedia = vi.fn(async () => {
    const track = new FakeTrack('cam-1');
    this.tracks.push(track);
    return { getTracks: () => [track], getVideoTracks: () => [track] };
  });
  enumerateDevices = vi.fn(async () => this.devices);
}

let video: FakeVideo;
let vision: VisionSystem;
let clock: number;
let media: FakeMediaDevices;

beforeEach(() => {
  vi.clearAllMocks();
  clock = 1000;
  vi.spyOn(performance, 'now').mockImplementation(() => clock);
  video = new FakeVideo();
  media = new FakeMediaDevices();
  vi.stubGlobal('document', { createElement: () => video, body: { appendChild: vi.fn() } });
  vi.stubGlobal('navigator', { mediaDevices: media });
  mediapipe.detectForVideo.mockImplementation(() => ({ landmarks: [makeBody({ noise: 0 })] }));
  vision = new VisionSystem();
});

afterEach(() => {
  vision.dispose();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

async function start(polling = false): Promise<void> {
  if (polling) Object.assign(video, { requestVideoFrameCallback: undefined });
  await vision.loadModel('lite');
  await vision.startCamera();
}

describe('VisionSystem camera frame delivery', () => {
  it('keeps pose and gesture time on the application clock when video callbacks recover', async () => {
    await start();
    video.present(1000, 0.1, 900);
    const first = vision.update(1000);
    video.currentTime = 0.2;
    const fallback = vision.update(1000 + C.rvfcStaleMs + 1);
    // Capture latency can put the next callback behind the previous polling update.
    video.present(1800, 0.3, 1600);
    const recovered = vision.update(1800);
    expect([first?.t, fallback?.t, recovered?.t]).toEqual([1000, 1701, 1800]);
    expect(mediapipe.detectForVideo.mock.calls.map((call) => call[1])).toEqual([1000, 1701, 1800]);
  });

  it('keeps tracking a live webcam whose callbacks report a constant mediaTime', async () => {
    await start();
    // Chrome can report the same mediaTime for every frame of a MediaStream.
    for (let i = 0; i < 5; i++) {
      video.present(1000 + i * 33, 0);
      vision.update(1000 + i * 33);
    }
    expect(mediapipe.detectForVideo).toHaveBeenCalledTimes(5);
  });

  it('keeps a newly polled frame available until the inference throttle allows it', async () => {
    await start(true);
    mediapipe.detectForVideo.mockImplementation(() => {
      clock += 20;
      return { landmarks: [makeBody({ noise: 0 })] };
    });
    const first = vision.update(1000);
    video.currentTime = 0.033;
    expect(vision.update(1016)).toBe(first);
    const next = vision.update(1033);
    expect(next?.t).toBe(1033);
    expect(mediapipe.detectForVideo).toHaveBeenCalledTimes(2);
  });

  it('does not infer the last callback frame again when switching to polling', async () => {
    await start();
    video.present(1000, 0.1);
    const first = vision.update(1000);
    expect(vision.update(1000 + C.rvfcStaleMs + 1)).toBe(first);
    expect(mediapipe.detectForVideo).toHaveBeenCalledTimes(1);
    video.currentTime = 0.2;
    expect(vision.update(1800)?.t).toBe(1800);
  });

  it('accepts the first frame after restarting a camera even at the same media time', async () => {
    await start(true);
    vision.update(1000);
    await vision.startCamera('replacement-camera');
    expect(vision.update(1100)?.t).toBe(1100);
    expect(mediapipe.detectForVideo).toHaveBeenCalledTimes(2);
  });

  it('surfaces repeated inference failures and recovers on a successful new frame', async () => {
    await start(true);
    mediapipe.detectForVideo.mockImplementation(() => { throw new Error('GPU unavailable'); });
    let i = 0;
    const failFrames = async (count: number): Promise<void> => {
      for (let k = 0; k < count; k++) {
        i++;
        video.currentTime = i / 30;
        expect(vision.update(1000 + i * 34)).toBeNull();
      }
      await Promise.resolve();
      await Promise.resolve();
    };
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    await failFrames(30);
    // First: one rebuild on the CPU delegate, no error yet.
    expect(vision.status).toBe('running');
    expect(vision.delegate).toBe('CPU');
    const create = vi.mocked(PoseLandmarker.createFromOptions);
    expect(create).toHaveBeenLastCalledWith(expect.anything(), expect.objectContaining({
      baseOptions: expect.objectContaining({ delegate: 'CPU' }),
    }));
    const calls = create.mock.calls.length;
    await failFrames(30);
    expect(create.mock.calls.length).toBe(calls); // no second fallback
    expect(vision.status).toBe('error');
    expect(vision.error).toContain('GPU unavailable');
    mediapipe.detectForVideo.mockReturnValue({ landmarks: [makeBody({ noise: 0 })] });
    video.currentTime = 5;
    expect(vision.update(4000)?.people).toHaveLength(1);
    expect(vision.status).toBe('running');
    expect(vision.error).toBeNull();
  });
});

describe('VisionSystem camera disconnects', () => {
  it('reports a disconnect when the video track ends, and startCamera recovers', async () => {
    await start(true);
    expect(vision.status).toBe('running');
    media.tracks[0].unplug();
    expect(vision.status).toBe('error');
    expect(vision.error).toBe(CAMERA_DISCONNECTED_MESSAGE);
    video.currentTime = 1;
    expect(vision.update(1100)).toBeNull();
    expect(mediapipe.detectForVideo).not.toHaveBeenCalled();

    await vision.startCamera();
    expect(vision.status).toBe('running');
    expect(vision.error).toBeNull();
    expect(vision.update(1200)?.t).toBe(1200);
    // The old track's listener is gone: a late 'ended' from it changes nothing.
    media.tracks[0].unplug();
    expect(vision.status).toBe('running');
  });

  it('reports a disconnect when the active device disappears from the device list', async () => {
    await start(true);
    media.devices = [];
    media.dispatchEvent(new Event('devicechange'));
    await vi.waitFor(() => expect(vision.status).toBe('error'));
    expect(vision.error).toBe(CAMERA_DISCONNECTED_MESSAGE);
    expect(media.tracks[0].stop).toHaveBeenCalled();
  });

  it('ignores devicechange when the active camera is still present', async () => {
    await start(true);
    media.devices = [...media.devices, { kind: 'videoinput', deviceId: 'cam-2', label: 'Other' }];
    media.dispatchEvent(new Event('devicechange'));
    await Promise.resolve();
    await Promise.resolve();
    expect(vision.status).toBe('running');
  });
});

describe('VisionSystem model loading', () => {
  it('shows a user-facing message when the model cannot be downloaded', async () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    vi.mocked(FilesetResolver.forVisionTasks).mockRejectedValueOnce(new Error('404 public/models/x.task'));
    await expect(vision.loadModel('lite')).rejects.toThrow(MODEL_DOWNLOAD_FAILED_MESSAGE);
    expect(vision.status).toBe('error');
    expect(vision.error).toBe(MODEL_DOWNLOAD_FAILED_MESSAGE);
  });
});

describe('VisionSystem with a CameraSource (door, ADR-0049)', () => {
  function fakeSource(): {
    source: CameraSource;
    track: FakeTrack;
    release: ReturnType<typeof vi.fn>;
    lose: () => void;
  } {
    const track = new FakeTrack('');
    const release = vi.fn();
    let lost: (() => void) | null = null;
    const source: CameraSource = {
      label: 'The door camera',
      open: vi.fn(async (onLost: () => void) => {
        lost = onLost;
        const stream = { getTracks: () => [track], getVideoTracks: () => [track] } as unknown as MediaStream;
        return { stream, release };
      }),
    };
    return { source, track, release, lose: () => lost?.() };
  }

  it('reads the source instead of opening a camera device', async () => {
    const { source } = fakeSource();
    vision.dispose();
    vision = new VisionSystem({ source });
    await start();
    expect(source.open).toHaveBeenCalledTimes(1);
    expect(media.getUserMedia).not.toHaveBeenCalled();
    expect(vision.status).toBe('running');
  });

  it('releases the source stream on dispose', async () => {
    const { source, release } = fakeSource();
    vision.dispose();
    vision = new VisionSystem({ source });
    await start();
    vision.dispose();
    expect(release).toHaveBeenCalledTimes(1);
  });

  it('reports a lost source stream as a disconnect and releases it', async () => {
    const { source, release, lose } = fakeSource();
    vision.dispose();
    vision = new VisionSystem({ source });
    await start();
    lose();
    expect(vision.status).toBe('error');
    expect(vision.error).toBe(CAMERA_DISCONNECTED_MESSAGE);
    expect(release).toHaveBeenCalled();
  });

  it('names the source when it cannot open', async () => {
    const source: CameraSource = { label: 'The door camera', open: vi.fn(async () => { throw new Error('stream visitor is unavailable'); }) };
    vision.dispose();
    vision = new VisionSystem({ source });
    await vision.loadModel('lite');
    await expect(vision.startCamera()).rejects.toThrow("The door camera isn't available right now (stream visitor is unavailable).");
    expect(vision.status).toBe('error');
  });

  it('caps inference at minInferenceIntervalMs', async () => {
    const { source } = fakeSource();
    vision.dispose();
    vision = new VisionSystem({ source, minInferenceIntervalMs: 50 });
    await start();
    // 40 fps of new frames for 200 ms → at most one inference per 50 ms.
    for (let t = 1000; t <= 1200; t += 25) {
      video.present(t, t / 1000);
      vision.update(t);
    }
    expect(mediapipe.detectForVideo).toHaveBeenCalledTimes(5);
  });
});
