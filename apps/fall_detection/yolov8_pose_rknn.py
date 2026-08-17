"""YOLOv8n-Pose RKNN inference adapter.

The RKNN output layout follows Rockchip's Apache-2.0 RKNN Model Zoo
``examples/yolov8_pose`` sample. Post-processing was rewritten and vectorized
for this project.
"""

from __future__ import annotations

from typing import List, Sequence, Tuple

import cv2
import numpy as np

from .heuristics import PoseDetection


def _sigmoid(value: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(value, -30.0, 30.0)))


def _softmax(value: np.ndarray, axis: int = -1) -> np.ndarray:
    shifted = value - np.max(value, axis=axis, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=axis, keepdims=True)


def _nms(boxes: np.ndarray, scores: np.ndarray, threshold: float) -> List[int]:
    if not len(boxes):
        return []
    x1, y1, x2, y2 = boxes.T
    areas = np.maximum(0.0, x2 - x1) * np.maximum(0.0, y2 - y1)
    order = scores.argsort()[::-1]
    keep: List[int] = []
    while order.size:
        current = int(order[0])
        keep.append(current)
        if order.size == 1:
            break
        rest = order[1:]
        xx1 = np.maximum(x1[current], x1[rest])
        yy1 = np.maximum(y1[current], y1[rest])
        xx2 = np.minimum(x2[current], x2[rest])
        yy2 = np.minimum(y2[current], y2[rest])
        intersection = np.maximum(0.0, xx2 - xx1) * np.maximum(0.0, yy2 - yy1)
        union = areas[current] + areas[rest] - intersection
        iou = np.divide(intersection, union, out=np.zeros_like(intersection), where=union > 0)
        order = rest[iou <= threshold]
    return keep


class YoloV8PoseRKNN:
    def __init__(
        self,
        model_path: str,
        object_threshold: float = 0.5,
        nms_threshold: float = 0.4,
        input_size: int = 640,
    ):
        try:
            from rknnlite.api import RKNNLite
        except ImportError as exc:
            raise RuntimeError(
                "rknn-toolkit-lite2 is required on RK3588; install the wheel matching the board Runtime"
            ) from exc

        self.object_threshold = object_threshold
        self.nms_threshold = nms_threshold
        self.input_size = input_size
        self.rknn = RKNNLite()
        ret = self.rknn.load_rknn(model_path)
        if ret != 0:
            raise RuntimeError(f"failed to load RKNN model {model_path}: {ret}")
        core_mask = getattr(RKNNLite, "NPU_CORE_0_1_2", None)
        ret = self.rknn.init_runtime(**({"core_mask": core_mask} if core_mask is not None else {}))
        if ret != 0:
            self.rknn.release()
            raise RuntimeError(f"failed to initialize RKNN runtime: {ret}")

    def close(self) -> None:
        if self.rknn is not None:
            self.rknn.release()
            self.rknn = None

    def __enter__(self) -> "YoloV8PoseRKNN":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _letterbox(self, image: np.ndarray) -> Tuple[np.ndarray, float, int, int]:
        height, width = image.shape[:2]
        scale = min(self.input_size / width, self.input_size / height)
        resized_width = max(1, int(round(width * scale)))
        resized_height = max(1, int(round(height * scale)))
        resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_AREA)
        canvas = np.full((self.input_size, self.input_size, 3), 56, dtype=np.uint8)
        offset_x = (self.input_size - resized_width) // 2
        offset_y = (self.input_size - resized_height) // 2
        canvas[offset_y:offset_y + resized_height, offset_x:offset_x + resized_width] = resized
        return canvas, scale, offset_x, offset_y

    def _decode(self, outputs: Sequence[np.ndarray]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        if len(outputs) < 4:
            raise RuntimeError(f"expected four YOLOv8-Pose outputs, got {len(outputs)}")
        keypoint_output = np.asarray(outputs[3])
        if keypoint_output.ndim == 3:
            keypoint_output = keypoint_output[0]
        all_boxes: List[np.ndarray] = []
        all_scores: List[np.ndarray] = []
        all_keypoints: List[np.ndarray] = []
        global_offset = 0

        heads = sorted((np.asarray(item) for item in outputs[:3]), key=lambda item: item.shape[-1], reverse=True)
        for head in heads:
            grid_h, grid_w = int(head.shape[-2]), int(head.shape[-1])
            stride = self.input_size // grid_w
            feature = head.reshape(65, -1)
            scores = _sigmoid(feature[64])
            selected = np.flatnonzero(scores >= self.object_threshold)
            if selected.size:
                logits = feature[:64, selected].T.reshape(-1, 4, 16)
                bins = np.arange(16, dtype=np.float32)
                distances = (_softmax(logits, axis=2) * bins).sum(axis=2)
                grid_x = selected % grid_w
                grid_y = selected // grid_w
                centers_x = grid_x.astype(np.float32) + 0.5
                centers_y = grid_y.astype(np.float32) + 0.5
                boxes = np.column_stack((
                    (centers_x - distances[:, 0]) * stride,
                    (centers_y - distances[:, 1]) * stride,
                    (centers_x + distances[:, 2]) * stride,
                    (centers_y + distances[:, 3]) * stride,
                ))
                positions = selected + global_offset
                keypoints = keypoint_output[:, positions].T.reshape(-1, 17, 3).copy()
                all_boxes.append(boxes)
                all_scores.append(scores[selected])
                all_keypoints.append(keypoints)
            global_offset += grid_h * grid_w

        if not all_boxes:
            return (
                np.empty((0, 4), dtype=np.float32),
                np.empty((0,), dtype=np.float32),
                np.empty((0, 17, 3), dtype=np.float32),
            )
        boxes = np.concatenate(all_boxes)
        scores = np.concatenate(all_scores)
        keypoints = np.concatenate(all_keypoints)
        keep = _nms(boxes, scores, self.nms_threshold)
        return boxes[keep], scores[keep], keypoints[keep]

    def infer(self, image: np.ndarray) -> List[PoseDetection]:
        model_image, scale, offset_x, offset_y = self._letterbox(image)
        rgb = cv2.cvtColor(model_image, cv2.COLOR_BGR2RGB)
        outputs = self.rknn.inference(inputs=[rgb[np.newaxis, ...]], data_format=["nhwc"])
        if outputs is None:
            raise RuntimeError("RKNN inference returned no outputs")
        boxes, scores, keypoints = self._decode(outputs)
        image_height, image_width = image.shape[:2]
        detections: List[PoseDetection] = []
        for box, score, points in zip(boxes, scores, keypoints):
            box[[0, 2]] = (box[[0, 2]] - offset_x) / scale
            box[[1, 3]] = (box[[1, 3]] - offset_y) / scale
            box[[0, 2]] = np.clip(box[[0, 2]], 0, image_width - 1)
            box[[1, 3]] = np.clip(box[[1, 3]], 0, image_height - 1)
            points[:, 0] = np.clip((points[:, 0] - offset_x) / scale, 0, image_width - 1)
            points[:, 1] = np.clip((points[:, 1] - offset_y) / scale, 0, image_height - 1)
            x1, y1, x2, y2 = box.tolist()
            detections.append(PoseDetection(
                box=(x1, y1, max(0.0, x2 - x1), max(0.0, y2 - y1)),
                score=float(score),
                keypoints=[(float(x), float(y), float(confidence)) for x, y, confidence in points],
            ))
        return detections
