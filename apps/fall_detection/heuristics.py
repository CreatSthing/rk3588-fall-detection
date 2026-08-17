from __future__ import annotations

import math
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Dict, Iterable, List, Optional, Sequence, Tuple


Keypoint = Tuple[float, float, float]
Box = Tuple[float, float, float, float]


@dataclass
class PoseDetection:
    box: Box
    score: float
    keypoints: List[Keypoint]
    track_id: Optional[int] = None


@dataclass
class Track:
    track_id: int
    detection: PoseDetection
    missed: int = 0
    history: Deque[Tuple[float, PoseDetection]] = field(default_factory=lambda: deque(maxlen=90))


def box_iou(left: Box, right: Box) -> float:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    x1 = max(lx, rx)
    y1 = max(ly, ry)
    x2 = min(lx + lw, rx + rw)
    y2 = min(ly + lh, ry + rh)
    intersection = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = max(0.0, lw * lh) + max(0.0, rw * rh) - intersection
    return intersection / union if union > 0 else 0.0


class SimplePoseTracker:
    """Small greedy IoU tracker suitable for a fixed indoor camera.

    It intentionally has no model dependency. ByteTrack can replace it later while
    keeping the same PoseDetection/track_id contract.
    """

    def __init__(self, iou_threshold: float = 0.25, max_missed: int = 15):
        self.iou_threshold = iou_threshold
        self.max_missed = max_missed
        self.next_id = 1
        self.tracks: Dict[int, Track] = {}

    def update(self, detections: Sequence[PoseDetection], timestamp: Optional[float] = None) -> List[PoseDetection]:
        now = time.time() if timestamp is None else timestamp
        candidates: List[Tuple[float, int, int]] = []
        for track_id, track in self.tracks.items():
            for detection_index, detection in enumerate(detections):
                candidates.append((box_iou(track.detection.box, detection.box), track_id, detection_index))

        matched_tracks = set()
        matched_detections = set()
        for iou, track_id, detection_index in sorted(candidates, reverse=True):
            if iou < self.iou_threshold:
                break
            if track_id in matched_tracks or detection_index in matched_detections:
                continue
            detection = detections[detection_index]
            detection.track_id = track_id
            track = self.tracks[track_id]
            track.detection = detection
            track.missed = 0
            track.history.append((now, detection))
            matched_tracks.add(track_id)
            matched_detections.add(detection_index)

        for track_id, track in list(self.tracks.items()):
            if track_id not in matched_tracks:
                track.missed += 1
                if track.missed > self.max_missed:
                    del self.tracks[track_id]

        for detection_index, detection in enumerate(detections):
            if detection_index in matched_detections:
                continue
            track_id = self.next_id
            self.next_id += 1
            detection.track_id = track_id
            track = Track(track_id=track_id, detection=detection)
            track.history.append((now, detection))
            self.tracks[track_id] = track

        return list(detections)


@dataclass
class FallState:
    phase: str = "normal"
    candidate_since: Optional[float] = None
    recovered_since: Optional[float] = None
    last_alert_at: float = -1e12
    event_id: Optional[str] = None
    seen_upright: bool = False
    motion_armed_until: float = -1e12


class FallDetector:
    """Explainable pose-based fall detector with temporal confirmation.

    A fall must combine a rapid downward movement with a horizontal/wide-body
    posture, then remain suspicious for ``confirm_seconds``. This is deliberately
    conservative and should be tuned on footage from the real installation.
    """

    LEFT_SHOULDER = 5
    RIGHT_SHOULDER = 6
    LEFT_HIP = 11
    RIGHT_HIP = 12

    def __init__(
        self,
        confirm_seconds: float = 0.7,
        recover_seconds: float = 2.0,
        cooldown_seconds: float = 30.0,
        keypoint_threshold: float = 0.25,
        descent_threshold: float = 0.45,
    ):
        self.confirm_seconds = confirm_seconds
        self.recover_seconds = recover_seconds
        self.cooldown_seconds = cooldown_seconds
        self.keypoint_threshold = keypoint_threshold
        self.descent_threshold = descent_threshold
        self.states: Dict[int, FallState] = {}
        self.history: Dict[int, Deque[Tuple[float, float, float]]] = {}

    def _center(self, keypoints: Sequence[Keypoint], first: int, second: int) -> Optional[Tuple[float, float]]:
        if len(keypoints) <= max(first, second):
            return None
        points = [keypoints[first], keypoints[second]]
        valid = [(x, y) for x, y, score in points if score >= self.keypoint_threshold]
        if not valid:
            return None
        return sum(point[0] for point in valid) / len(valid), sum(point[1] for point in valid) / len(valid)

    def features(self, detection: PoseDetection, timestamp: float) -> Dict[str, float | bool]:
        _, _, width, height = detection.box
        safe_height = max(height, 1.0)
        aspect_ratio = width / safe_height
        shoulder = self._center(detection.keypoints, self.LEFT_SHOULDER, self.RIGHT_SHOULDER)
        hip = self._center(detection.keypoints, self.LEFT_HIP, self.RIGHT_HIP)

        torso_angle = 0.0
        hip_y = detection.box[1] + height * 0.62
        if hip is not None:
            hip_y = hip[1]
        if shoulder is not None and hip is not None:
            dx = abs(hip[0] - shoulder[0])
            dy = abs(hip[1] - shoulder[1])
            torso_angle = math.degrees(math.atan2(dx, max(dy, 1e-6)))

        track_id = int(detection.track_id or 0)
        samples = self.history.setdefault(track_id, deque(maxlen=45))
        samples.append((timestamp, hip_y, safe_height))
        while samples and timestamp - samples[0][0] > 0.8:
            samples.popleft()

        descent_speed = 0.0
        if len(samples) >= 2:
            old_time, old_y, old_height = samples[0]
            elapsed = timestamp - old_time
            if elapsed >= 0.15:
                descent_speed = (hip_y - old_y) / max(old_height, safe_height, 1.0) / elapsed

        horizontal = torso_angle >= 52.0 or aspect_ratio >= 0.95
        rapid_descent = descent_speed >= self.descent_threshold
        lying = torso_angle >= 62.0 or aspect_ratio >= 1.18
        suspicious = horizontal and (rapid_descent or lying)
        score = min(1.0, max(0.0,
            0.45 * min(descent_speed / max(self.descent_threshold, 1e-6), 1.0)
            + 0.30 * min(torso_angle / 75.0, 1.0)
            + 0.25 * min(aspect_ratio / 1.25, 1.0)
        ))
        return {
            "aspect_ratio": round(aspect_ratio, 3),
            "torso_angle": round(torso_angle, 2),
            "descent_speed": round(descent_speed, 3),
            "horizontal": horizontal,
            "rapid_descent": rapid_descent,
            "lying": lying,
            "suspicious": suspicious,
            "score": round(score, 3),
        }

    def update(self, detection: PoseDetection, timestamp: Optional[float] = None) -> Tuple[str, Dict[str, object], Optional[Dict[str, object]]]:
        now = time.time() if timestamp is None else timestamp
        if detection.track_id is None:
            raise ValueError("fall detection requires a track_id")
        track_id = detection.track_id
        state = self.states.setdefault(track_id, FallState())
        features = self.features(detection, now)
        if not bool(features["horizontal"]):
            state.seen_upright = True
        if bool(features["horizontal"]) and bool(features["rapid_descent"]):
            state.motion_armed_until = now + max(1.5, self.confirm_seconds + 0.3)
        suspicious = bool(features["horizontal"]) and state.seen_upright and now <= state.motion_armed_until
        features["suspicious"] = suspicious
        event: Optional[Dict[str, object]] = None

        if suspicious:
            state.recovered_since = None
            if state.phase == "normal":
                state.phase = "candidate"
                state.candidate_since = now
            elif state.phase == "candidate" and state.candidate_since is not None:
                if now - state.candidate_since >= self.confirm_seconds and now - state.last_alert_at >= self.cooldown_seconds:
                    state.phase = "fallen"
                    state.last_alert_at = now
                    state.event_id = uuid.uuid4().hex
                    event = {
                        "id": state.event_id,
                        "event_type": "fall",
                        "state": "confirmed",
                        "track_id": track_id,
                        "confidence": features["score"],
                        "timestamp": now,
                        "details": features,
                    }
        else:
            if state.phase == "candidate":
                state.phase = "normal"
                state.candidate_since = None
            elif state.phase == "fallen":
                if state.recovered_since is None:
                    state.recovered_since = now
                elif now - state.recovered_since >= self.recover_seconds:
                    state.phase = "normal"
                    state.candidate_since = None
                    state.recovered_since = None
                    if state.event_id:
                        event = {
                            "id": state.event_id,
                            "event_type": "fall",
                            "state": "recovered",
                            "track_id": track_id,
                            "confidence": features["score"],
                            "timestamp": now,
                            "details": features,
                        }
                    state.event_id = None

        action = "fall" if state.phase == "fallen" else ("suspected_fall" if state.phase == "candidate" else "normal")
        return action, features, event

    def prune(self, active_track_ids: Iterable[int]) -> None:
        active = set(active_track_ids)
        for track_id in list(self.states):
            if track_id not in active:
                self.states.pop(track_id, None)
                self.history.pop(track_id, None)
