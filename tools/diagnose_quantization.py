#!/usr/bin/env python3
"""Diagnose where YOLOv5 RKNN INT8 quantization loses accuracy."""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import cv2
import numpy as np
from rknn.api import RKNN

TOOLS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TOOLS_DIR.parent
sys.path.insert(0, str(TOOLS_DIR))

from validate_rknn_models import (  # noqa: E402
    IOU_THRESHOLD,
    PERSON_CLASS,
    SCORE_THRESHOLD,
    average_precision,
    iou_matrix,
    load_ground_truth,
    match_predictions,
)
from yolov5_convert import decode_yolov5_outputs  # noqa: E402


IMG_SIZE = 640


def parse_args() -> argparse.Namespace:
    calibration_root = PROJECT_ROOT / "assets" / "calibration"
    parser = argparse.ArgumentParser(
        description=(
            "Build FP and INT8 RKNN simulator graphs from one ONNX model, compare "
            "raw outputs and person detection quality, and audit calibration data."
        )
    )
    parser.add_argument(
        "--onnx",
        type=Path,
        default=PROJECT_ROOT / "assets" / "models" / "yolov5s.onnx",
        help="Source YOLOv5 ONNX model.",
    )
    parser.add_argument(
        "--calibration-dataset",
        type=Path,
        default=calibration_root / "coco128_calibration.txt",
        help="Calibration txt used for INT8 build.",
    )
    parser.add_argument(
        "--validation-list",
        type=Path,
        default=calibration_root / "coco128_validation.txt",
        help="Validation image list. Relative entries default to --image-root.",
    )
    parser.add_argument(
        "--image-root",
        type=Path,
        default=calibration_root,
        help="Base directory for relative entries in calibration and validation txt files.",
    )
    parser.add_argument(
        "--label-dir",
        type=Path,
        default=calibration_root / "coco128" / "labels" / "train2017",
        help="YOLO label directory used for person metrics and dataset audit.",
    )
    parser.add_argument(
        "--calibration-label-dir",
        type=Path,
        default=calibration_root / "coco128" / "labels" / "train2017",
        help="YOLO label directory used only to audit the calibration list.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=PROJECT_ROOT / "output" / "pc_validation" / "quantization_diagnosis.json",
        help="JSON diagnosis report output path.",
    )
    parser.add_argument("--target", default="rk3588", help="RKNN target platform.")
    parser.add_argument("--input-name", default="images", help="ONNX input tensor name.")
    parser.add_argument("--input-size", type=int, default=IMG_SIZE, help="Square model input size.")
    parser.add_argument("--limit", type=int, default=0, help="Limit validation images for a quick run.")
    parser.add_argument("--verbose-rknn", action="store_true", help="Show verbose RKNN build logs.")
    return parser.parse_args()


def read_image_list(list_path: Path, image_root: Path, limit: int = 0) -> List[Path]:
    paths = [
        (image_root / line.strip()).resolve()
        for line in list_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if limit > 0:
        paths = paths[:limit]
    return paths


def audit_dataset(name: str, list_path: Path, image_root: Path, label_dir: Path) -> Dict[str, object]:
    entries = read_image_list(list_path, image_root)
    missing_images = [str(path) for path in entries if not path.exists()]
    images_with_person = 0
    person_boxes = 0
    all_boxes = 0
    person_area_ratios = []

    for image_path in entries:
        label_path = label_dir / f"{image_path.stem}.txt"
        if not label_path.exists():
            continue
        has_person = False
        for line in label_path.read_text(encoding="utf-8").splitlines():
            fields = line.split()
            if len(fields) < 5:
                continue
            all_boxes += 1
            class_id = int(fields[0])
            box_width = float(fields[3])
            box_height = float(fields[4])
            if class_id == PERSON_CLASS:
                has_person = True
                person_boxes += 1
                person_area_ratios.append(box_width * box_height)
        if has_person:
            images_with_person += 1

    areas = np.asarray(person_area_ratios, dtype=np.float32)
    small_person_boxes = int(np.sum(areas < 0.01)) if len(areas) else 0
    medium_person_boxes = int(np.sum((areas >= 0.01) & (areas < 0.08))) if len(areas) else 0
    large_person_boxes = int(np.sum(areas >= 0.08)) if len(areas) else 0
    return {
        "name": name,
        "list": str(list_path),
        "image_root": str(image_root),
        "entries": len(entries),
        "missing_images": missing_images[:20],
        "missing_image_count": len(missing_images),
        "images_with_person": images_with_person,
        "images_without_person": len(entries) - images_with_person,
        "all_label_boxes": all_boxes,
        "person_boxes": person_boxes,
        "person_area_ratio_mean": float(areas.mean()) if len(areas) else 0.0,
        "person_area_ratio_p50": float(np.percentile(areas, 50)) if len(areas) else 0.0,
        "person_area_ratio_p10": float(np.percentile(areas, 10)) if len(areas) else 0.0,
        "small_person_boxes_area_lt_1pct": small_person_boxes,
        "medium_person_boxes_area_1pct_to_8pct": medium_person_boxes,
        "large_person_boxes_area_gte_8pct": large_person_boxes,
    }


def build_runtime(args: argparse.Namespace, quantize: bool) -> RKNN:
    rknn = RKNN(verbose=args.verbose_rknn)
    ret = rknn.config(
        mean_values=[[0, 0, 0]],
        std_values=[[255, 255, 255]],
        target_platform=args.target,
    )
    if ret != 0:
        raise RuntimeError(f"rknn.config failed: {ret}")

    ret = rknn.load_onnx(
        model=str(args.onnx),
        inputs=[args.input_name],
        input_size_list=[[1, 3, args.input_size, args.input_size]],
    )
    if ret != 0:
        raise RuntimeError(f"rknn.load_onnx failed: {ret}")

    ret = rknn.build(
        do_quantization=quantize,
        dataset=str(args.calibration_dataset) if quantize else None,
    )
    if ret != 0:
        raise RuntimeError(f"rknn.build failed: {ret}")

    ret = rknn.init_runtime()
    if ret != 0:
        raise RuntimeError(f"rknn.init_runtime failed: {ret}")
    return rknn


def preprocess(image_path: Path, input_size: int) -> np.ndarray:
    image = cv2.imread(str(image_path))
    if image is None:
        raise RuntimeError(f"OpenCV could not read image: {image_path}")
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image = cv2.resize(image, (input_size, input_size))
    return np.expand_dims(image, axis=0)


def output_specs(outputs: Iterable[np.ndarray]) -> List[Dict[str, object]]:
    specs = []
    for output in outputs:
        array = np.asarray(output)
        specs.append(
            {
                "shape": list(array.shape),
                "dtype": str(array.dtype),
                "min": float(array.min()),
                "max": float(array.max()),
                "mean": float(array.mean()),
                "std": float(array.std()),
            }
        )
    return specs


def compare_raw_outputs(fp_outputs: List[np.ndarray], int8_outputs: List[np.ndarray]) -> List[Dict[str, object]]:
    comparisons = []
    for index, (fp_output, int8_output) in enumerate(zip(fp_outputs, int8_outputs)):
        fp_array = np.asarray(fp_output, dtype=np.float32)
        int8_array = np.asarray(int8_output, dtype=np.float32)
        item: Dict[str, object] = {
            "index": index,
            "fp_shape": list(fp_array.shape),
            "int8_shape": list(int8_array.shape),
            "same_shape": fp_array.shape == int8_array.shape,
        }
        if fp_array.shape == int8_array.shape:
            diff = int8_array - fp_array
            flat_fp = fp_array.reshape(-1)
            flat_int8 = int8_array.reshape(-1)
            denom = float(np.linalg.norm(flat_fp) * np.linalg.norm(flat_int8))
            item.update(
                {
                    "mae": float(np.mean(np.abs(diff))),
                    "rmse": float(np.sqrt(np.mean(diff * diff))),
                    "max_abs": float(np.max(np.abs(diff))),
                    "cosine": float(np.dot(flat_fp, flat_int8) / denom) if denom > 0 else 0.0,
                }
            )
        comparisons.append(item)
    return comparisons


def decode_person(outputs: List[np.ndarray], input_size: int) -> Tuple[np.ndarray, np.ndarray]:
    boxes, classes, scores = decode_yolov5_outputs(outputs, input_size)
    if boxes is None:
        return np.empty((0, 4), dtype=np.float32), np.empty((0,), dtype=np.float32)
    person_mask = (classes == PERSON_CLASS) & (scores >= SCORE_THRESHOLD)
    return boxes[person_mask].astype(np.float32), scores[person_mask].astype(np.float32)


def update_eval_state(state: Dict[str, object], boxes: np.ndarray, scores: np.ndarray, targets: np.ndarray) -> Dict[str, int]:
    tp_flags, tp, fp, fn = match_predictions(boxes, scores, targets)
    state["true_positive"] += tp
    state["false_positive"] += fp
    state["false_negative"] += fn
    state["scores"].extend(scores.tolist())
    state["tp_flags"].extend(tp_flags.tolist())
    state["prediction_counts"].append(len(boxes))
    state["best_scores"].append(float(scores.max()) if len(scores) else 0.0)
    return {"tp": tp, "fp": fp, "fn": fn}


def summarize_eval(state: Dict[str, object], target_count: int) -> Dict[str, object]:
    tp = int(state["true_positive"])
    fp = int(state["false_positive"])
    fn = int(state["false_negative"])
    precision = tp / max(tp + fp, 1)
    recall = tp / max(tp + fn, 1)
    f1 = 2 * precision * recall / max(precision + recall, 1e-8)
    counts = np.asarray(state["prediction_counts"], dtype=np.float32)
    best_scores = np.asarray(state["best_scores"], dtype=np.float32)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "ap50": average_precision(state["scores"], state["tp_flags"], target_count),
        "prediction_count_mean": float(counts.mean()) if len(counts) else 0.0,
        "best_person_score_mean": float(best_scores.mean()) if len(best_scores) else 0.0,
        "best_person_score_p50": float(np.percentile(best_scores, 50)) if len(best_scores) else 0.0,
    }


def summarize_raw_diff(rows: List[Dict[str, object]]) -> List[Dict[str, object]]:
    if not rows:
        return []
    output_count = max(len(row["raw_output_diff"]) for row in rows)
    summary = []
    for index in range(output_count):
        items = [
            row["raw_output_diff"][index]
            for row in rows
            if index < len(row["raw_output_diff"]) and row["raw_output_diff"][index].get("same_shape")
        ]
        if not items:
            continue
        summary.append(
            {
                "index": index,
                "mae_mean": float(np.mean([item["mae"] for item in items])),
                "rmse_mean": float(np.mean([item["rmse"] for item in items])),
                "max_abs_p95": float(np.percentile([item["max_abs"] for item in items], 95)),
                "cosine_mean": float(np.mean([item["cosine"] for item in items])),
                "cosine_min": float(np.min([item["cosine"] for item in items])),
            }
        )
    return summary


def pick_worst_cases(rows: List[Dict[str, object]]) -> Dict[str, List[Dict[str, object]]]:
    missed = [
        row for row in rows
        if row["ground_truth_person_boxes"] > 0 and row["fp"]["tp"] > row["int8"]["tp"]
    ]
    confidence_drop = [
        row for row in rows
        if row["fp_best_person_score"] - row["int8_best_person_score"] > 0.1
    ]
    false_positives = [
        row for row in rows
        if row["ground_truth_person_boxes"] == 0 and row["int8"]["fp"] > row["fp"]["fp"]
    ]
    raw_outliers = sorted(
        rows,
        key=lambda row: max(
            item.get("mae", 0.0)
            for item in row["raw_output_diff"]
            if item.get("same_shape")
        ),
        reverse=True,
    )
    compact_keys = (
        "image",
        "ground_truth_person_boxes",
        "fp_best_person_score",
        "int8_best_person_score",
        "score_drop",
        "fp",
        "int8",
    )

    def compact(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
        return [{key: item[key] for key in compact_keys} for item in items[:10]]

    return {
        "int8_missed_fp_hits": compact(
            sorted(
                missed,
                key=lambda row: (row["fp"]["tp"] - row["int8"]["tp"], row["score_drop"]),
                reverse=True,
            )
        ),
        "confidence_drop": compact(sorted(confidence_drop, key=lambda row: row["score_drop"], reverse=True)),
        "new_int8_false_positives": compact(sorted(false_positives, key=lambda row: row["int8"]["fp"], reverse=True)),
        "raw_output_outliers": compact(raw_outliers),
    }


def main() -> int:
    args = parse_args()
    args.onnx = args.onnx.resolve()
    args.calibration_dataset = args.calibration_dataset.resolve()
    validation_list = args.validation_list.resolve()
    image_root = args.image_root.resolve()
    label_dir = args.label_dir.resolve()
    calibration_label_dir = args.calibration_label_dir.resolve()

    for path in (args.onnx, args.calibration_dataset, validation_list, image_root, label_dir, calibration_label_dir):
        if not path.exists():
            raise FileNotFoundError(f"required input not found: {path}")

    calibration_entries = set(
        line.strip()
        for line in args.calibration_dataset.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    validation_entries = set(
        line.strip()
        for line in validation_list.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    image_paths = read_image_list(validation_list, image_root, args.limit)
    missing_validation_images = [path for path in image_paths if not path.exists()]
    if missing_validation_images:
        raise FileNotFoundError(f"validation image not found: {missing_validation_images[0]}")

    print("Building FP simulator graph...")
    fp_rknn = build_runtime(args, quantize=False)
    print("Building INT8 simulator graph...")
    int8_rknn = build_runtime(args, quantize=True)

    target_count = 0
    fp_state = {
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0,
        "scores": [],
        "tp_flags": [],
        "prediction_counts": [],
        "best_scores": [],
    }
    int8_state = {
        "true_positive": 0,
        "false_positive": 0,
        "false_negative": 0,
        "scores": [],
        "tp_flags": [],
        "prediction_counts": [],
        "best_scores": [],
    }
    rows = []
    fp_spec: Optional[List[Dict[str, object]]] = None
    int8_spec: Optional[List[Dict[str, object]]] = None

    try:
        for index, image_path in enumerate(image_paths, 1):
            model_input = preprocess(image_path, args.input_size)
            targets = load_ground_truth(label_dir / f"{image_path.stem}.txt", args.input_size, args.input_size)
            target_count += len(targets)

            fp_outputs = fp_rknn.inference(inputs=[model_input])
            int8_outputs = int8_rknn.inference(inputs=[model_input])
            if fp_spec is None:
                fp_spec = output_specs(fp_outputs)
                int8_spec = output_specs(int8_outputs)

            fp_boxes, fp_scores = decode_person(fp_outputs, args.input_size)
            int8_boxes, int8_scores = decode_person(int8_outputs, args.input_size)
            fp_match = update_eval_state(fp_state, fp_boxes, fp_scores, targets)
            int8_match = update_eval_state(int8_state, int8_boxes, int8_scores, targets)

            raw_diff = compare_raw_outputs(fp_outputs, int8_outputs)
            row = {
                "image": str(image_path),
                "ground_truth_person_boxes": int(len(targets)),
                "fp_person_predictions": int(len(fp_boxes)),
                "int8_person_predictions": int(len(int8_boxes)),
                "fp_best_person_score": float(fp_scores.max()) if len(fp_scores) else 0.0,
                "int8_best_person_score": float(int8_scores.max()) if len(int8_scores) else 0.0,
                "fp": fp_match,
                "int8": int8_match,
                "raw_output_diff": raw_diff,
            }
            row["score_drop"] = row["fp_best_person_score"] - row["int8_best_person_score"]
            if len(fp_boxes) and len(int8_boxes):
                row["best_box_iou"] = float(iou_matrix(fp_boxes[:1], int8_boxes[:1]).max())
            else:
                row["best_box_iou"] = 0.0
            rows.append(row)
            print(f"[{index}/{len(image_paths)}] {image_path.name}")
    finally:
        fp_rknn.release()
        int8_rknn.release()

    report = {
        "inputs": {
            "onnx": str(args.onnx),
            "calibration_dataset": str(args.calibration_dataset),
            "validation_list": str(validation_list),
            "image_root": str(image_root),
            "label_dir": str(label_dir),
            "calibration_label_dir": str(calibration_label_dir),
            "score_threshold": SCORE_THRESHOLD,
            "iou_threshold": IOU_THRESHOLD,
            "validation_images": len(image_paths),
        },
        "dataset_audit": {
            "calibration": audit_dataset("calibration", args.calibration_dataset, image_root, calibration_label_dir),
            "validation": audit_dataset("validation", validation_list, image_root, label_dir),
            "calibration_validation_overlap_count": len(calibration_entries & validation_entries),
        },
        "output_specs": {
            "fp": fp_spec,
            "int8": int8_spec,
        },
        "metrics": {
            "ground_truth_person_boxes": target_count,
            "fp": summarize_eval(fp_state, target_count),
            "int8": summarize_eval(int8_state, target_count),
        },
        "raw_output_diff_summary": summarize_raw_diff(rows),
        "worst_cases": pick_worst_cases(rows),
        "per_image": rows,
    }
    report["metrics"]["delta_int8_minus_fp"] = {
        key: report["metrics"]["int8"][key] - report["metrics"]["fp"][key]
        for key in ("precision", "recall", "f1", "ap50", "best_person_score_mean")
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in ("dataset_audit", "metrics", "raw_output_diff_summary", "worst_cases")}, indent=2))
    print(f"report: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
