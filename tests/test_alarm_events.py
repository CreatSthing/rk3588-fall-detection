import tempfile
import unittest
from pathlib import Path

from apps.web.backend.events import EventRepository


class AlarmEventRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repository = EventRepository(Path(self.temp_dir.name) / "events.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_event_lifecycle_is_persistent(self):
        saved = self.repository.upsert({
            "id": "fall-1",
            "camera_id": "cam1",
            "track_id": 7,
            "event_type": "fall",
            "state": "confirmed",
            "confidence": 0.92,
            "timestamp": 1000.0,
            "details": {"torso_angle": 72},
        })
        self.assertFalse(saved["acknowledged"])
        self.assertEqual("pending", saved["recording_status"])

        recorded = self.repository.set_recording("fall-1", "ready", "/tmp/fall-1.mp4")
        self.assertTrue(recorded["video_ready"])
        acknowledged = self.repository.acknowledge("fall-1")
        self.assertTrue(acknowledged["acknowledged"])

        recovered = self.repository.upsert({
            "id": "fall-1",
            "camera_id": "cam1",
            "track_id": 7,
            "state": "recovered",
            "confidence": 0.2,
            "timestamp": 1010.0,
        })
        self.assertEqual("recovered", recovered["state"])
        self.assertEqual(1010.0, recovered["ended_at"])
        self.assertEqual(1, len(self.repository.list()))


if __name__ == "__main__":
    unittest.main()
