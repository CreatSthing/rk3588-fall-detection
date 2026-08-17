import unittest

from apps.fall_detection.video_source import _parse_fps, is_network_source


class VideoSourceTests(unittest.TestCase):
    def test_network_source_detection(self):
        self.assertTrue(is_network_source("rtsp://127.0.0.1/live"))
        self.assertTrue(is_network_source("HTTPS://example.test/video.mp4"))
        self.assertFalse(is_network_source("sample.mp4"))
        self.assertFalse(is_network_source(0))

    def test_fps_fraction_and_fallback(self):
        self.assertAlmostEqual(29.97, _parse_fps("30000/1001", 15.0), places=2)
        self.assertEqual(15.0, _parse_fps("0/0", 15.0))
        self.assertEqual(15.0, _parse_fps("unknown", 15.0))


if __name__ == "__main__":
    unittest.main()
