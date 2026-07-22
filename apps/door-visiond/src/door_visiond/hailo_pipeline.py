"""Shared Hailo face pipeline: SCRFD detect -> align -> ArcFace embed (T-305).

This module is the single home of the on-device ML recipe validated on a
Hailo-8 (HailoRT 4.23).  Both :class:`~door_visiond.embedder.HailoEmbedder`
(enrollment stills) and :class:`~door_visiond.pipeline.HardwareBackend` (live
frames) drive the *same* :class:`HailoFacePipeline` instance so the VDevice and
the two configured network groups are created once and reused for the life of
the process — never opened or closed per frame.

Hardware-absent safety: this module imports ``cv2``/``hailo_platform`` (device
runtime, ``system-site-packages`` on the Pi) and is therefore imported *lazily*
by its callers.  ``numpy`` is a normal dependency.  Nothing here is imported at
the top level of ``embedder``/``pipeline``/``compat``, so mock/disabled/CI runs
stay import-safe.

The vector produced here is a raw ``tuple[float, ...]``; callers immediately
wrap it in :class:`~door_visiond.embedding.Embedding`.  It is L2-normalized and
never logged or serialized.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass

import cv2  # type: ignore[import-not-found]
import numpy as np

# ArcFace canonical 5-point destination landmarks for a 112x112 crop.
ARCFACE_DST = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)

_SCRFD_INPUT = 640  # SCRFD square input side (letterboxed).
_ARCFACE_INPUT = 112  # ArcFace aligned crop side.
_STRIDES = (8, 16, 32)  # SCRFD feature strides -> feature sizes 80/40/20.
_NUM_ANCHORS = 2  # anchors per feature-map cell.
_SCORE_THRESHOLD = 0.5  # scores are already sigmoid'd (0-1); no extra sigmoid.
_NMS_IOU = 0.4


@dataclass(frozen=True)
class PipelineFace:
    """One detected + embedded face.

    ``vector`` is the L2-normalized 512-d ArcFace embedding as a plain tuple;
    the caller wraps it in ``Embedding`` at once.  ``score`` is the raw SCRFD
    detection confidence (0-1); ``size_px`` is the face bounding-box height in
    original-image pixels.
    """

    vector: tuple[float, ...]
    score: float
    size_px: int


def _letterbox(img_bgr: np.ndarray) -> tuple[np.ndarray, float]:
    """Resize *img_bgr* into a 640x640 canvas keeping aspect ratio.

    The image is pasted at the top-left (no centering offset) so original
    coordinates are recovered by dividing detections by the returned scale.
    """
    h, w = img_bgr.shape[:2]
    scale = min(_SCRFD_INPUT / h, _SCRFD_INPUT / w)
    new_w, new_h = int(round(w * scale)), int(round(h * scale))
    resized = cv2.resize(img_bgr, (new_w, new_h), interpolation=cv2.INTER_LINEAR)
    canvas = np.zeros((_SCRFD_INPUT, _SCRFD_INPUT, 3), dtype=np.uint8)
    canvas[:new_h, :new_w] = resized
    return canvas, scale


def _anchor_centers(feat: int, stride: int) -> np.ndarray:
    """(feat*feat*num_anchors, 2) anchor centers in (x, y) pixel coords.

    Ordering matches a row-major flatten of an (H, W, anchor) tensor: y-major,
    then x, then anchor (each cell repeated ``_NUM_ANCHORS`` times).
    """
    xs = np.arange(feat)
    ys = np.arange(feat)
    grid_x, grid_y = np.meshgrid(xs, ys)  # 'xy' -> shape (feat, feat)
    centers = np.stack([grid_x, grid_y], axis=-1).astype(np.float32).reshape(-1, 2)
    centers = centers * stride
    return np.repeat(centers, _NUM_ANCHORS, axis=0)


def _iou_nms(boxes: np.ndarray, scores: np.ndarray, iou_thresh: float) -> list[int]:
    """Plain IoU non-max-suppression; returns kept indices, highest score first."""
    if boxes.shape[0] == 0:
        return []
    x1, y1, x2, y2 = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[i], x1[rest])
        yy1 = np.maximum(y1[i], y1[rest])
        xx2 = np.minimum(x2[i], x2[rest])
        yy2 = np.minimum(y2[i], y2[rest])
        inter = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[i] + areas[rest] - inter
        iou = np.where(union > 0.0, inter / union, 0.0)
        order = rest[iou <= iou_thresh]
    return keep


@dataclass(frozen=True)
class _Detection:
    bbox: tuple[float, float, float, float]  # x1, y1, x2, y2 in original coords
    score: float
    landmarks: np.ndarray  # (5, 2) float32 in original coords


class HailoFacePipeline:
    """Persistent SCRFD + ArcFace pipeline over a single Hailo VDevice.

    Construction is cheap and hardware-free; the VDevice + both network groups
    are opened lazily on first inference and then reused.  All device access is
    serialized with a lock because a single Hailo VDevice is not safe to drive
    from multiple threads concurrently (the run loop calls it from a worker
    thread, enrollment from the request thread).
    """

    def __init__(
        self,
        *,
        detector_hef_path: str,
        recognizer_hef_path: str,
        model_id: str,
        dim: int = 512,
    ) -> None:
        self._detector_hef_path = detector_hef_path
        self._recognizer_hef_path = recognizer_hef_path
        self._model_id = model_id
        self._dim = dim
        self._lock = threading.Lock()
        self._opened = False
        # Persistent handles, populated by _ensure_open().
        self._exit_stack: object | None = None
        self._det_ng: object | None = None
        self._det_pipe: object | None = None
        self._det_input_name: str = ""
        self._rec_ng: object | None = None
        self._rec_pipe: object | None = None
        self._rec_input_name: str = ""

    @property
    def model_id(self) -> str:
        return self._model_id

    @property
    def dim(self) -> int:
        return self._dim

    # -- device lifecycle --------------------------------------------------

    def _ensure_open(self) -> None:
        if self._opened:
            return
        import contextlib

        from hailo_platform import (  # type: ignore[import-not-found]
            HEF,
            ConfigureParams,
            FormatType,
            HailoStreamInterface,
            InferVStreams,
            InputVStreamParams,
            OutputVStreamParams,
            VDevice,
        )

        stack = contextlib.ExitStack()
        try:
            vdevice = stack.enter_context(VDevice())

            def _configure(path: str) -> tuple[object, object, str]:
                hef = HEF(path)
                cfg = ConfigureParams.create_from_hef(hef, interface=HailoStreamInterface.PCIe)
                network_group = vdevice.configure(hef, cfg)[0]
                in_params = InputVStreamParams.make(network_group, format_type=FormatType.UINT8)
                out_params = OutputVStreamParams.make(network_group, format_type=FormatType.FLOAT32)
                pipe = stack.enter_context(InferVStreams(network_group, in_params, out_params))
                input_name = hef.get_input_vstream_infos()[0].name
                return network_group, pipe, input_name

            self._det_ng, self._det_pipe, self._det_input_name = _configure(self._detector_hef_path)
            self._rec_ng, self._rec_pipe, self._rec_input_name = _configure(
                self._recognizer_hef_path
            )
        except Exception:
            stack.close()
            raise
        self._exit_stack = stack
        self._opened = True

    def close(self) -> None:
        with self._lock:
            if self._exit_stack is not None:
                self._exit_stack.close()  # type: ignore[attr-defined]
            self._exit_stack = None
            self._opened = False

    # -- inference primitives ----------------------------------------------

    def _infer(self, network_group: object, pipe: object, input_name: str, data: np.ndarray):
        # Keep the VDevice + network groups persistent; only (de)activate the
        # network group around each infer call, matching the validated recipe.
        with network_group.activate():  # type: ignore[attr-defined]
            return pipe.infer({input_name: data})  # type: ignore[attr-defined]

    def _detect(self, img_bgr: np.ndarray) -> list[_Detection]:
        canvas, scale = _letterbox(img_bgr)
        # Models bake normalization; feed RGB uint8 NHWC.
        rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        nhwc = rgb[np.newaxis, ...].astype(np.uint8)
        result = self._infer(self._det_ng, self._det_pipe, self._det_input_name, nhwc)

        # Group outputs by feature size (H) then channel count (C, last dim).
        # Assumes HailoRT delivers FLOAT32 output vstreams as NHWC (H, W, C after
        # squeezing the batch); verify on-device if the SCRFD HEF was exported
        # with a different output order.
        by_feat: dict[int, dict[int, np.ndarray]] = {}
        for arr in result.values():
            squeezed = np.squeeze(np.asarray(arr))
            if squeezed.ndim != 3:
                continue
            h, _w, c = squeezed.shape
            by_feat.setdefault(int(h), {})[int(c)] = squeezed

        boxes_all: list[np.ndarray] = []
        scores_all: list[np.ndarray] = []
        kps_all: list[np.ndarray] = []
        for stride in _STRIDES:
            feat = _SCRFD_INPUT // stride
            group = by_feat.get(feat)
            if group is None or not {2, 8, 20} <= set(group):
                continue
            scores = group[2].reshape(-1)
            bbox = group[8].reshape(-1, 4)
            kps = group[20].reshape(-1, 10)
            centers = _anchor_centers(feat, stride)
            cx = centers[:, 0]
            cy = centers[:, 1]

            x1 = cx - bbox[:, 0] * stride
            y1 = cy - bbox[:, 1] * stride
            x2 = cx + bbox[:, 2] * stride
            y2 = cy + bbox[:, 3] * stride
            boxes = np.stack([x1, y1, x2, y2], axis=-1)

            lmk_x = cx[:, np.newaxis] + kps[:, 0::2] * stride
            lmk_y = cy[:, np.newaxis] + kps[:, 1::2] * stride
            landmarks = np.stack([lmk_x, lmk_y], axis=-1)  # (N, 5, 2)

            keep = scores >= _SCORE_THRESHOLD
            boxes_all.append(boxes[keep])
            scores_all.append(scores[keep])
            kps_all.append(landmarks[keep])

        if not boxes_all:
            return []
        boxes = np.concatenate(boxes_all, axis=0) / scale
        scores = np.concatenate(scores_all, axis=0)
        landmarks = np.concatenate(kps_all, axis=0) / scale
        if boxes.shape[0] == 0:
            return []

        kept = _iou_nms(boxes, scores, _NMS_IOU)
        detections: list[_Detection] = []
        for i in kept:
            box = boxes[i]
            detections.append(
                _Detection(
                    bbox=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
                    score=float(scores[i]),
                    landmarks=landmarks[i].astype(np.float32),
                )
            )
        return detections

    def _embed(self, img_bgr: np.ndarray, det: _Detection) -> tuple[float, ...]:
        matrix, _inliers = cv2.estimateAffinePartial2D(det.landmarks, ARCFACE_DST)
        aligned = cv2.warpAffine(img_bgr, matrix, (_ARCFACE_INPUT, _ARCFACE_INPUT))
        rgb = cv2.cvtColor(aligned, cv2.COLOR_BGR2RGB)
        nhwc = rgb[np.newaxis, ...].astype(np.uint8)
        result = self._infer(self._rec_ng, self._rec_pipe, self._rec_input_name, nhwc)
        raw = np.asarray(next(iter(result.values()))).reshape(-1).astype(np.float32)
        norm = float(np.linalg.norm(raw))
        if norm == 0.0:
            return tuple(float(v) for v in raw)
        unit = raw / norm  # ArcFace raw norm ~2.2 -> L2-normalize to unit length.
        return tuple(float(v) for v in unit)

    @staticmethod
    def _size_px(det: _Detection) -> int:
        return max(0, int(round(det.bbox[3] - det.bbox[1])))

    @staticmethod
    def _quality(det: _Detection) -> float:
        # Detection score is already a 0-1 confidence; clamp defensively.
        return max(0.0, min(1.0, det.score))

    # -- public API used by the adapters -----------------------------------

    def decode(self, image_bytes: bytes) -> np.ndarray | None:
        """Decode encoded image bytes (e.g. JPEG) to a BGR ndarray, or None."""
        buf = np.frombuffer(image_bytes, dtype=np.uint8)
        if buf.size == 0:
            return None
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None or img.size == 0:
            return None
        return img

    def embed_primary(self, image_bytes: bytes) -> PipelineFace | None:
        """Detect faces in *image_bytes*, embed the LARGEST, or return None.

        Used for enrollment stills: no face -> None so the caller can emit a
        rejecting (low) quality.
        """
        img = self.decode(image_bytes)
        if img is None:
            return None
        with self._lock:
            self._ensure_open()
            detections = self._detect(img)
            if not detections:
                return None
            largest = max(detections, key=self._size_px)
            vector = self._embed(img, largest)
        return PipelineFace(
            vector=vector, score=self._quality(largest), size_px=self._size_px(largest)
        )

    def embed_all(self, image_bytes: bytes) -> tuple[list[PipelineFace], float]:
        """Detect + embed EVERY face in *image_bytes*.

        Returns ``(faces, inference_ms)`` where ``inference_ms`` is the measured
        wall-clock of the detect + embed work (excludes image transport).
        """
        img = self.decode(image_bytes)
        if img is None:
            return [], 0.0
        with self._lock:
            self._ensure_open()
            start = time.perf_counter()
            detections = self._detect(img)
            faces = [
                PipelineFace(
                    vector=self._embed(img, det),
                    score=self._quality(det),
                    size_px=self._size_px(det),
                )
                for det in detections
            ]
            inference_ms = (time.perf_counter() - start) * 1000.0
        return faces, inference_ms
