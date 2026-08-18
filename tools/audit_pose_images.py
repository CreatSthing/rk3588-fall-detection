"""Run the deployed YOLOv8-Pose RKNN model over calibration candidates."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from apps.fall_detection.yolov8_pose_rknn import YoloV8PoseRKNN


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--images", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--threshold", type=float, default=0.25)
    args = parser.parse_args()

    images = sorted(path for path in args.images.rglob("*") if path.suffix.lower() in {".jpg", ".jpeg", ".png"})
    results = []
    with YoloV8PoseRKNN(str(args.model), object_threshold=args.threshold) as model:
        for path in images:
            image = cv2.imread(str(path))
            if image is None:
                results.append({"path": str(path), "error": "decode failed", "detections": []})
                continue
            detections = model.infer(image)
            results.append({
                "path": str(path),
                "detections": [
                    {
                        "score": round(item.score, 6),
                        "box": [round(value, 2) for value in item.box],
                        "keypoints": [
                            [round(x, 2), round(y, 2), round(score, 6)]
                            for x, y, score in item.keypoints
                        ],
                    }
                    for item in detections
                ],
            })
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "images": len(results),
        "with_person": sum(bool(item["detections"]) for item in results),
        "output": str(args.output),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
