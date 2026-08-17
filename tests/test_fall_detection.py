import unittest

import numpy as np

from apps.fall_detection.heuristics import FallDetector, PoseDetection, SimplePoseTracker
from apps.fall_detection.yolov8_pose_rknn import _normalize_keypoint_output


def pose(box, hip_y, horizontal=False):
    x, y, w, h = box
    points = [(0.0, 0.0, 0.0)] * 17
    if horizontal:
        points[5] = (x + w * 0.3, hip_y, 0.9)
        points[6] = (x + w * 0.35, hip_y, 0.9)
        points[11] = (x + w * 0.7, hip_y + 2, 0.9)
        points[12] = (x + w * 0.75, hip_y + 2, 0.9)
    else:
        points[5] = (x + w * 0.45, hip_y - h * 0.35, 0.9)
        points[6] = (x + w * 0.55, hip_y - h * 0.35, 0.9)
        points[11] = (x + w * 0.47, hip_y, 0.9)
        points[12] = (x + w * 0.53, hip_y, 0.9)
    return PoseDetection(box=box, score=0.9, keypoints=list(points), track_id=1)


class FallDetectorTests(unittest.TestCase):
    def test_confirms_rapid_fall_once(self):
        detector = FallDetector(confirm_seconds=0.5, descent_threshold=0.35, cooldown_seconds=10)
        event = None
        samples = [
            (0.0, pose((100, 50, 80, 240), 210, False)),
            (0.2, pose((100, 90, 100, 210), 250, False)),
            (0.4, pose((90, 180, 230, 100), 285, True)),
            (0.7, pose((90, 180, 230, 100), 287, True)),
            (1.0, pose((90, 180, 230, 100), 287, True)),
        ]
        actions = []
        for timestamp, detection in samples:
            action, _, current = detector.update(detection, timestamp)
            actions.append(action)
            event = current or event
        self.assertEqual("fall_down", actions[-1])
        self.assertIsNotNone(event)
        self.assertEqual("confirmed", event["state"])

    def test_does_not_alarm_for_person_already_lying(self):
        detector = FallDetector(confirm_seconds=0.2)
        events = []
        for index in range(10):
            _, _, event = detector.update(pose((80, 180, 240, 90), 230, True), index * 0.1)
            if event:
                events.append(event)
        self.assertEqual([], events)

    def test_tracker_keeps_identity(self):
        tracker = SimplePoseTracker(iou_threshold=0.2)
        first = PoseDetection((10, 10, 80, 180), 0.9, [])
        second = PoseDetection((15, 12, 80, 180), 0.9, [])
        tracker.update([first], 0.0)
        tracker.update([second], 0.1)
        self.assertEqual(first.track_id, second.track_id)

    def test_tracker_keeps_identity_when_box_rotates_during_fall(self):
        tracker = SimplePoseTracker(iou_threshold=0.25)
        upright = PoseDetection((100, 40, 80, 240), 0.9, [])
        horizontal = PoseDetection((70, 170, 230, 90), 0.9, [])
        tracker.update([upright], 0.0)
        tracker.update([horizontal], 0.2)
        self.assertEqual(upright.track_id, horizontal.track_id)

    def test_exposes_upstream_seven_action_states(self):
        self.assertEqual(
            {"standing", "walking", "sitting", "lying_down", "stand_up", "sit_down", "fall_down"},
            set(FallDetector.ACTION_LABELS),
        )

    def test_splits_stand_up_and_sit_down_by_vertical_direction(self):
        base = {
            "lying": False,
            "torso_angle": 35.0,
            "knee_angle": 110.0,
            "aspect_ratio": 0.7,
            "horizontal_speed": 0.0,
        }
        self.assertEqual("stand_up", FallDetector.classify_posture({**base, "descent_speed": -0.25}))
        self.assertEqual("sit_down", FallDetector.classify_posture({**base, "descent_speed": 0.25}))

    def test_normalizes_board_keypoint_tensor(self):
        output = np.zeros((1, 17, 3, 8400), dtype=np.float32)
        normalized = _normalize_keypoint_output(output)
        self.assertEqual((51, 8400), normalized.shape)


if __name__ == "__main__":
    unittest.main()
