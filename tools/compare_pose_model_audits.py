"""Compare matching FP and INT8 YOLOv8-Pose image audit results."""

from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List


def keyed(path: Path) -> Dict[str, dict]:
    values = json.loads(path.read_text(encoding="utf-8"))
    return {Path(item["path"]).name: item for item in values}


def mean(values: List[float]) -> float:
    return statistics.mean(values) if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("fp", type=Path)
    parser.add_argument("int8", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    fp, quant = keyed(args.fp), keyed(args.int8)
    names = sorted(fp.keys() & quant.keys())
    score_diff, coordinate_diff, keypoint_score_diff = [], [], []
    fp_scores, quant_scores = [], []
    matched = 0
    for name in names:
        left = fp[name].get("detections") or []
        right = quant[name].get("detections") or []
        if not left or not right:
            continue
        matched += 1
        left, right = left[0], right[0]
        fp_scores.append(float(left["score"]))
        quant_scores.append(float(right["score"]))
        score_diff.append(abs(float(left["score"]) - float(right["score"])))
        for lp, rp in zip(left.get("keypoints") or [], right.get("keypoints") or []):
            coordinate_diff.append(math.hypot(float(lp[0]) - float(rp[0]), float(lp[1]) - float(rp[1])))
            keypoint_score_diff.append(abs(float(lp[2]) - float(rp[2])))

    report = {
        "images": len(names),
        "fp_detected": sum(bool(fp[name].get("detections")) for name in names),
        "int8_detected": sum(bool(quant[name].get("detections")) for name in names),
        "matched_top_detection": matched,
        "fp_top_score_mean": round(mean(fp_scores), 6),
        "int8_top_score_mean": round(mean(quant_scores), 6),
        "top_score_mae": round(mean(score_diff), 6),
        "keypoint_coordinate_mae_px": round(mean(coordinate_diff), 4),
        "keypoint_score_mae": round(mean(keypoint_score_diff), 6),
        "int8_exact_0_5_scores": sum(score == 0.5 for score in quant_scores),
        "int8_score_range": [round(min(quant_scores), 6), round(max(quant_scores), 6)] if quant_scores else [],
    }
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
