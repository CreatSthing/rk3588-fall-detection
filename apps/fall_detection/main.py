from __future__ import annotations

import argparse
import concurrent.futures
import json
import shutil
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Deque, Dict, List, Tuple

import cv2
import numpy as np

from .heuristics import FallDetector, SimplePoseTracker
from .video_source import is_network_source, open_video_source
from .yolov8_pose_rknn import YoloV8PoseRKNN


class EventClipBuffer:
    """Keeps compressed pre-event frames and writes clips off the inference loop."""

    def __init__(self, output_dir: Path, fps: float, pre_seconds: float, post_seconds: float):
        self.output_dir = output_dir
        self.fps = max(1.0, min(fps, 60.0))
        self.post_seconds = post_seconds
        self.buffer: Deque[Tuple[float, bytes]] = deque(maxlen=max(1, int(self.fps * pre_seconds)))
        self.active: Dict[str, Dict[str, object]] = {}
        self.futures: Dict[concurrent.futures.Future, Dict[str, object]] = {}
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="event-clip")

    @staticmethod
    def _write_clip(path: Path, fps: float, frames: List[bytes]) -> Tuple[bool, str]:
        if not frames:
            return False, "no buffered frames"
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg:
            command = [
                ffmpeg,
                "-hide_banner", "-loglevel", "error", "-y",
                "-f", "image2pipe", "-vcodec", "mjpeg", "-framerate", f"{fps:.3f}",
                "-i", "pipe:0", "-an", "-c:v", "libx264", "-preset", "veryfast",
                "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(path),
            ]
            completed = subprocess.run(command, input=b"".join(frames), capture_output=True)
            if completed.returncode == 0 and path.exists() and path.stat().st_size > 0:
                return True, ""
            error = completed.stderr.decode("utf-8", errors="replace").strip()[-1000:]
        else:
            error = "ffmpeg not found"
        first = cv2.imdecode(np.frombuffer(frames[0], dtype=np.uint8), cv2.IMREAD_COLOR)
        if first is None:
            return False, "cannot decode buffered frame"
        writer = None
        for codec in ("avc1", "H264", "mp4v"):
            candidate = cv2.VideoWriter(
                str(path), cv2.VideoWriter_fourcc(*codec), fps, (first.shape[1], first.shape[0])
            )
            if candidate.isOpened():
                writer = candidate
                break
            candidate.release()
        if writer is None:
            return False, f"FFmpeg failed ({error}); OpenCV cannot open MP4 event writer"
        try:
            for encoded in frames:
                frame = cv2.imdecode(np.frombuffer(encoded, dtype=np.uint8), cv2.IMREAD_COLOR)
                if frame is not None:
                    writer.write(frame)
        finally:
            writer.release()
        return path.exists() and path.stat().st_size > 0, ""

    def add_frame(self, timestamp: float, frame) -> List[Dict[str, object]]:
        ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if ok:
            item = (timestamp, encoded.tobytes())
            self.buffer.append(item)
            for recording in self.active.values():
                recording["frames"].append(item[1])

        for event_id, recording in list(self.active.items()):
            if timestamp < float(recording["deadline"]):
                continue
            future = self.executor.submit(
                self._write_clip,
                Path(str(recording["path"])),
                self.fps,
                list(recording["frames"]),
            )
            self.futures[future] = recording
            del self.active[event_id]
        return self.poll()

    def start(self, event: Dict[str, object], timestamp: float) -> Path:
        event_id = str(event["id"])
        path = self.output_dir / f"{time.strftime('%Y%m%d-%H%M%S', time.localtime(timestamp))}-{event_id}.mp4"
        self.active[event_id] = {
            "id": event_id,
            "track_id": event.get("track_id"),
            "timestamp": event.get("timestamp") or timestamp,
            "path": path,
            "deadline": timestamp + self.post_seconds,
            "frames": [encoded for _, encoded in self.buffer],
        }
        return path

    def poll(self) -> List[Dict[str, object]]:
        updates: List[Dict[str, object]] = []
        for future, recording in list(self.futures.items()):
            if not future.done():
                continue
            try:
                ready, error = future.result()
            except Exception as exc:
                ready, error = False, str(exc)
            updates.append({
                "id": recording["id"],
                "event_type": "fall",
                "state": "confirmed",
                "track_id": recording.get("track_id"),
                "timestamp": recording["timestamp"],
                "video_path": str(recording["path"]),
                "recording_status": "ready" if ready else "failed",
                "recording_error": error or None,
            })
            del self.futures[future]
        return updates

    def close(self) -> List[Dict[str, object]]:
        for event_id, recording in list(self.active.items()):
            future = self.executor.submit(
                self._write_clip,
                Path(str(recording["path"])),
                self.fps,
                list(recording["frames"]),
            )
            self.futures[future] = recording
            del self.active[event_id]
        self.executor.shutdown(wait=True)
        return self.poll()


def parse_source(value: str):
    return int(value) if value.isdigit() else value


def detection_json(detection, action: str, features: Dict[str, object]) -> Dict[str, object]:
    x, y, width, height = detection.box
    return {
        "label": "person",
        "score": round(detection.score, 4),
        "track_id": detection.track_id,
        "action": action,
        "action_label": features["action_label"],
        "fall_state": features["fall_state"],
        "fall_score": features["score"],
        "pose_features": features,
        "box": {"x": round(x, 1), "y": round(y, 1), "w": round(width, 1), "h": round(height, 1)},
        "keypoints": [
            {"x": round(px, 1), "y": round(py, 1), "score": round(score, 3)}
            for px, py, score in detection.keypoints
        ],
    }


def run(args: argparse.Namespace) -> int:
    capture = open_video_source(parse_source(args.source), args.decoder, args.record_fps)
    tracker = SimplePoseTracker(iou_threshold=args.track_iou, max_missed=args.max_missed)
    fall_detector = FallDetector(
        confirm_seconds=args.confirm_seconds,
        recover_seconds=args.recover_seconds,
        cooldown_seconds=args.cooldown_seconds,
        descent_threshold=args.descent_threshold,
    )
    output_dir = Path(args.event_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_fps = capture.fps
    clip_buffer = EventClipBuffer(output_dir, source_fps, args.pre_event_seconds, args.post_event_seconds)
    frame_id = 0
    started_at = time.monotonic()
    timeline_started_at = time.time()
    source_is_live = isinstance(parse_source(args.source), int) or is_network_source(args.source)

    try:
        with YoloV8PoseRKNN(
            args.model,
            object_threshold=args.object_threshold,
            nms_threshold=args.nms_threshold,
        ) as pose_model:
            while True:
                ok, frame = capture.read()
                if not ok:
                    break
                frame_id += 1
                now = time.time() if source_is_live else timeline_started_at + (frame_id - 1) / max(source_fps, 1.0)
                clip_updates = clip_buffer.add_frame(now, frame)
                poses = tracker.update(pose_model.infer(frame), timestamp=now)
                detections: List[Dict[str, object]] = []
                events: List[Dict[str, object]] = []
                for pose in poses:
                    action, features, event = fall_detector.update(pose, timestamp=now)
                    detections.append(detection_json(pose, action, features))
                    if event:
                        event["camera_id"] = args.camera_id
                        x, y, width, height = pose.box
                        event["box"] = {"x": x, "y": y, "w": width, "h": height}
                        if event["state"] == "confirmed":
                            event["video_path"] = str(clip_buffer.start(event, now))
                            event["recording_status"] = "recording"
                        events.append(event)
                events.extend(clip_updates)
                fall_detector.prune(tracker.tracks.keys())
                elapsed = max(time.monotonic() - started_at, 1e-6)
                payload = {
                    "camera_id": args.camera_id,
                    "frame_id": frame_id,
                    "timestamp": now,
                    "fps": round(frame_id / elapsed, 2),
                    "width": int(frame.shape[1]),
                    "height": int(frame.shape[0]),
                    "detections": detections,
                    "events": events,
                }
                print(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), flush=True)
                if args.max_frames and frame_id >= args.max_frames:
                    break
    finally:
        capture.close()
        final_updates = clip_buffer.close()
        if final_updates:
            print(json.dumps({
                "camera_id": args.camera_id,
                "frame_id": frame_id,
                "timestamp": time.time(),
                "detections": [],
                "events": final_updates,
            }, ensure_ascii=False, separators=(",", ":")), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RK3588 YOLOv8-Pose fall detection pipeline")
    parser.add_argument("--model", required=True, help="yolov8n-pose INT8 RKNN model")
    parser.add_argument("--source", required=True, help="RTSP URL, video path, or camera index")
    parser.add_argument("--camera-id", default="cam1")
    parser.add_argument("--event-dir", default="/var/lib/rk3588-camera/events")
    parser.add_argument("--object-threshold", type=float, default=0.5)
    parser.add_argument("--nms-threshold", type=float, default=0.4)
    parser.add_argument("--track-iou", type=float, default=0.25)
    parser.add_argument("--max-missed", type=int, default=15)
    parser.add_argument("--confirm-seconds", type=float, default=0.7)
    parser.add_argument("--recover-seconds", type=float, default=2.0)
    parser.add_argument("--cooldown-seconds", type=float, default=30.0)
    parser.add_argument(
        "--descent-threshold",
        type=float,
        default=0.22,
        help="normalized hip descent per second; calibrated on the bundled offline fall test",
    )
    parser.add_argument("--max-frames", type=int, default=0)
    parser.add_argument("--pre-event-seconds", type=float, default=5.0)
    parser.add_argument("--post-event-seconds", type=float, default=10.0)
    parser.add_argument("--record-fps", type=float, default=15.0)
    parser.add_argument(
        "--decoder",
        choices=("auto", "opencv", "ffmpeg-software"),
        default="auto",
        help="auto uses FFmpeg software decoding for network streams to avoid RKMPP/RGA stride failures",
    )
    return parser


if __name__ == "__main__":
    try:
        raise SystemExit(run(build_parser().parse_args()))
    except Exception as exc:
        print(json.dumps({"level": "error", "message": str(exc)}), file=sys.stderr, flush=True)
        raise
