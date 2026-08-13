#!/usr/bin/env python3
"""Compare FP and INT8 RKNN models on an independent YOLO-format validation set."""

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

import cv2
import numpy as np
from rknn.api import RKNN

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

from yolov5_convert import decode_yolov5_outputs  # noqa: E402


IMG_SIZE = 640
PERSON_CLASS = 0
IOU_THRESHOLD = 0.5
SCORE_THRESHOLD = 0.25


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Compare FP and INT8 RKNN models on a YOLO validation list."
    )
    parser.add_argument("--onnx", type=Path, required=True, help="Source ONNX model.")
    parser.add_argument("--fp-model", type=Path, required=True, help="Exported FP RKNN model to check.")
    parser.add_argument("--int8-model", type=Path, required=True, help="Exported INT8 RKNN model to check.")
    parser.add_argument(
        "--calibration-dataset",
        type=Path,
        default=PROJECT_ROOT / "assets" / "calibration" / "coco128_calibration.txt",
        help="Calibration list used to rebuild the INT8 model in the PC simulator.",
    )
    parser.add_argument(
        "--labels",
        type=Path,
        default=PROJECT_ROOT / "assets" / "calibration" / "coco128_validation.txt",
        help="Validation image list. Labels are found beside the COCO dataset.",
    )
    parser.add_argument(
        "--label-dir",
        type=Path,
        default=PROJECT_ROOT / "assets" / "calibration" / "coco128" / "labels" / "train2017",
        help="YOLO label directory.",
    )
    parser.add_argument(
        "--image-root",
        type=Path,
        default=PROJECT_ROOT / "assets" / "calibration",
        help="Base directory for relative image paths in the validation list.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "output" / "pc_validation" / "rknn_validation.json",
        help="JSON report output path.",
    )
    return parser.parse_args()


def load_ground_truth(label_path: Path, width: int, height: int) -> np.ndarray:
    if not label_path.exists():
        return np.empty((0, 4), dtype=np.float32)

    boxes = []
    for line in label_path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) < 5 or int(fields[0]) != PERSON_CLASS:
            continue
        _, x_center, y_center, box_width, box_height = map(float, fields[:5])
        x1 = (x_center - box_width / 2) * width
        y1 = (y_center - box_height / 2) * height
        x2 = (x_center + box_width / 2) * width
        y2 = (y_center + box_height / 2) * height
        boxes.append([x1, y1, x2, y2])
    return np.asarray(boxes, dtype=np.float32).reshape(-1, 4)


def decode_outputs(outputs: List[np.ndarray]) -> Tuple[np.ndarray, np.ndarray]:
    boxes, classes, scores = decode_yolov5_outputs(outputs, IMG_SIZE)
    if boxes is None:
        return np.empty((0, 4), dtype=np.float32), np.empty((0,), dtype=np.float32)

    person_mask = classes == PERSON_CLASS
    return boxes[person_mask].astype(np.float32), scores[person_mask].astype(np.float32)


def iou_matrix(predictions: np.ndarray, targets: np.ndarray) -> np.ndarray:
    if len(predictions) == 0 or len(targets) == 0:
        return np.zeros((len(predictions), len(targets)), dtype=np.float32)

    top_left = np.maximum(predictions[:, None, :2], targets[None, :, :2])
    bottom_right = np.minimum(predictions[:, None, 2:], targets[None, :, 2:])
    intersection_size = np.maximum(0, bottom_right - top_left)
    intersection = intersection_size[..., 0] * intersection_size[..., 1]

    pred_area = np.maximum(0, predictions[:, 2] - predictions[:, 0]) * np.maximum(
        0, predictions[:, 3] - predictions[:, 1]
    )
    target_area = np.maximum(0, targets[:, 2] - targets[:, 0]) * np.maximum(
        0, targets[:, 3] - targets[:, 1]
    )
    union = pred_area[:, None] + target_area[None, :] - intersection
    return intersection / np.maximum(union, 1e-8)


def match_predictions(
    predictions: np.ndarray, scores: np.ndarray, targets: np.ndarray
) -> Tuple[np.ndarray, int, int, int]:
    order = scores.argsort()[::-1]
    predictions = predictions[order]
    overlaps = iou_matrix(predictions, targets)
    matched_targets = set()
    sorted_true_positive = np.zeros(len(predictions), dtype=np.int32)

    for prediction_index in range(len(predictions)):
        if len(targets) == 0:
            break
        target_index = int(np.argmax(overlaps[prediction_index]))
        if (
            overlaps[prediction_index, target_index] >= IOU_THRESHOLD
            and target_index not in matched_targets
        ):
            matched_targets.add(target_index)
            sorted_true_positive[prediction_index] = 1

    true_positive = np.zeros(len(predictions), dtype=np.int32)
    true_positive[order] = sorted_true_positive
    false_positive = len(predictions) - int(sorted_true_positive.sum())
    false_negative = len(targets) - len(matched_targets)
    return true_positive, int(sorted_true_positive.sum()), false_positive, false_negative


def average_precision(scores: List[float], true_positive: List[int], target_count: int) -> float:
    if target_count == 0:
        return 0.0
    order = np.argsort(np.asarray(scores))[::-1]
    tp = np.asarray(true_positive, dtype=np.float32)[order]
    fp = 1.0 - tp
    precision = np.cumsum(tp) / np.maximum(np.cumsum(tp) + np.cumsum(fp), 1e-8)
    recall = np.cumsum(tp) / target_count

    recall_levels = np.linspace(0, 1, 101)
    interpolated = []
    for recall_level in recall_levels:
        valid = precision[recall >= recall_level]
        interpolated.append(float(valid.max()) if len(valid) else 0.0)
    return float(np.mean(interpolated))


def evaluate_model(
    model_path: Path,
    onnx_path: Path,
    calibration_dataset: Path,
    quantize: bool,
    image_paths: List[Path],
    label_dir: Path,
) -> Dict[str, object]:
    exported = RKNN(verbose=False)
    ret = exported.load_rknn(str(model_path))
    if ret != 0:
        raise RuntimeError(f"load_rknn failed for {model_path}: {ret}")
    exported.release()

    # The x86 simulator cannot execute a loaded .rknn file. Rebuild the same
    # FP/INT8 graph from ONNX in this process, then run it with init_runtime().
    rknn = RKNN(verbose=False)
    ret = rknn.config(
        mean_values=[[0, 0, 0]],
        std_values=[[255, 255, 255]],
        target_platform="rk3588",
    )
    if ret != 0:
        raise RuntimeError(f"config failed for {model_path}: {ret}")
    ret = rknn.load_onnx(
        model=str(onnx_path),
        inputs=["images"],
        input_size_list=[[1, 3, IMG_SIZE, IMG_SIZE]],
    )
    if ret != 0:
        raise RuntimeError(f"load_onnx failed for {model_path}: {ret}")
    ret = rknn.build(
        do_quantization=quantize,
        dataset=str(calibration_dataset) if quantize else None,
    )
    if ret != 0:
        raise RuntimeError(f"build failed for {model_path}: {ret}")
    ret = rknn.init_runtime()
    if ret != 0:
        raise RuntimeError(f"init_runtime failed for {model_path}: {ret}")

    total_true_positive = 0
    total_false_positive = 0
    total_false_negative = 0
    target_count = 0
    all_scores = []
    all_true_positive = []
    inference_times = []
    images_with_person = 0
    first_output_spec = None

    try:
        for image_path in image_paths:
            image = cv2.imread(str(image_path))
            if image is None:
                raise RuntimeError(f"could not read validation image: {image_path}")
            height, width = image.shape[:2]
            label_path = label_dir / f"{image_path.stem}.txt"
            targets = load_ground_truth(label_path, IMG_SIZE, IMG_SIZE)
            if len(targets):
                images_with_person += 1
            target_count += len(targets)

            image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
            image = cv2.resize(image, (IMG_SIZE, IMG_SIZE))
            start = time.perf_counter()
            outputs = rknn.inference(inputs=[np.expand_dims(image, axis=0)])
            inference_times.append((time.perf_counter() - start) * 1000)
            if first_output_spec is None:
                first_output_spec = [
                    {
                        "shape": list(np.asarray(output).shape),
                        "dtype": str(np.asarray(output).dtype),
                        "min": float(np.asarray(output).min()),
                        "max": float(np.asarray(output).max()),
                    }
                    for output in outputs
                ]
            boxes, scores = decode_outputs(outputs)
            keep = scores >= SCORE_THRESHOLD
            boxes, scores = boxes[keep], scores[keep]

            tp_flags, tp, fp, fn = match_predictions(boxes, scores, targets)
            total_true_positive += tp
            total_false_positive += fp
            total_false_negative += fn
            all_scores.extend(scores.tolist())
            all_true_positive.extend(tp_flags.tolist())
    finally:
        rknn.release()

    precision = total_true_positive / max(total_true_positive + total_false_positive, 1)
    recall = total_true_positive / max(total_true_positive + total_false_negative, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-8)
    return {
        "model": str(model_path),
        "images": len(image_paths),
        "images_with_person": images_with_person,
        "ground_truth_person_boxes": target_count,
        "true_positive": total_true_positive,
        "false_positive": total_false_positive,
        "false_negative": total_false_negative,
        "precision_at_0.25_iou_0.5": precision,
        "recall_at_0.25_iou_0.5": recall,
        "f1_at_0.25_iou_0.5": f1,
        "ap50_at_0.25_score": average_precision(
            all_scores, all_true_positive, target_count
        ),
        "mean_inference_ms": float(np.mean(inference_times)),
        "p95_inference_ms": float(np.percentile(inference_times, 95)),
        "first_output_spec": first_output_spec,
    }


def main() -> int:
    args = parse_args()
    args.onnx = args.onnx.resolve()
    args.fp_model = args.fp_model.resolve()
    args.int8_model = args.int8_model.resolve()
    args.calibration_dataset = args.calibration_dataset.resolve()
    validation_list = args.labels.resolve()
    image_root = args.image_root.resolve()
    label_dir = args.label_dir.resolve()
    for path in (args.onnx, args.fp_model, args.int8_model, args.calibration_dataset, validation_list):
        if not path.exists():
            raise FileNotFoundError(f"required validation input not found: {path}")
    image_paths = [
        (image_root / line.strip()).resolve()
        for line in validation_list.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    for image_path in image_paths:
        if not image_path.exists():
            raise FileNotFoundError(f"validation image not found: {image_path}")

    results = [
        evaluate_model(
            args.fp_model,
            args.onnx,
            args.calibration_dataset,
            False,
            image_paths,
            label_dir,
        ),
        evaluate_model(
            args.int8_model,
            args.onnx,
            args.calibration_dataset,
            True,
            image_paths,
            label_dir,
        ),
    ]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps({"validation_list": str(validation_list), "results": results}, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"results": results}, indent=2))
    print(f"report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
