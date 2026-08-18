"""Summarize JSONL output from the fall-detection CLI."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", type=Path)
    args = parser.parse_args()

    payloads = []
    for line in args.jsonl.read_text(encoding="utf-8").splitlines():
        try:
            payloads.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    frame_payloads = [item for item in payloads if item.get("frame_id")]
    scores = [
        float(detection.get("score") or 0)
        for item in frame_payloads
        for detection in item.get("detections") or []
    ]
    events = [event for item in payloads for event in item.get("events") or []]
    confirmed = [event for event in events if event.get("state") == "confirmed" and event.get("recording_status") == "recording"]
    ready = [event for event in events if event.get("recording_status") == "ready"]
    npu_times = [
        float((item.get("timings_ms") or {}).get("npu") or 0)
        for item in frame_payloads
        if (item.get("timings_ms") or {}).get("npu") is not None
    ]
    report = {
        "frames": max((int(item.get("frame_id") or 0) for item in frame_payloads), default=0),
        "frames_with_detection": sum(bool(item.get("detections")) for item in frame_payloads),
        "detection_score_mean": round(statistics.mean(scores), 6) if scores else 0.0,
        "detection_score_range": [round(min(scores), 6), round(max(scores), 6)] if scores else [],
        "exact_0_5_scores": sum(score == 0.5 for score in scores),
        "final_fps": next(
            (float(item.get("fps")) for item in reversed(frame_payloads) if item.get("fps") is not None),
            0.0,
        ),
        "npu_ms_mean": round(statistics.mean(npu_times), 3) if npu_times else 0.0,
        "confirmed_events": len(confirmed),
        "confirmed_confidences": [event.get("confidence") for event in confirmed],
        "ready_recordings": len(ready),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
