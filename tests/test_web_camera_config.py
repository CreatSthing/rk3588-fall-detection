import unittest

from apps.web.backend.app import (
    CameraUpsertRequest,
    camera_source_url,
    parse_npu_cores,
    parse_rga_schedulers,
    update_camera_config,
)


class WebCameraConfigTests(unittest.TestCase):
    def test_parses_rockchip_npu_and_rga_per_core_loads(self):
        npu = parse_npu_cores("NPU load:  Core0: 42%, Core1:  4%, Core2:  3%,")
        self.assertEqual([42.0, 4.0, 3.0], [item["load_percent"] for item in npu])
        rga = parse_rga_schedulers(
            "scheduler[0]: rga3_core0\n load = 7%\nscheduler[1]: rga2\n load = 0%\n"
        )
        self.assertEqual([7.0, 0.0], [item["load_percent"] for item in rga])

    def test_source_url_falls_back_to_stream_command(self):
        camera = {"stream_command": ["push-stream", "rtsp://old-camera/stream2", "rtsp://local/live"]}
        self.assertEqual(camera_source_url(camera), "rtsp://old-camera/stream2")

    def test_update_preserves_deployment_specific_camera_settings(self):
        camera = {
            "id": "cam1",
            "name": "old",
            "stream_command": ["push-stream", "rtsp://old-camera/stream2", "rtsp://local/live"],
            "pipeline_command": ["python", "-m", "apps.fall_detection.main", "--source", "{source}"],
            "record_command": ["record-event"],
            "auto_start_stream": True,
            "auto_start_pipeline": True,
        }
        request = CameraUpsertRequest(
            id="cam1",
            name="front door",
            source_url="rtsp://new-camera/stream2",
            width=1280,
            height=720,
            contexts=3,
            decoder="auto",
        )

        updated = update_camera_config(camera, request)

        self.assertEqual(updated["stream_command"][1], "rtsp://new-camera/stream2")
        self.assertEqual(updated["pipeline_command"], camera["pipeline_command"])
        self.assertEqual(updated["record_command"], camera["record_command"])
        self.assertTrue(updated["auto_start_stream"])
        self.assertTrue(updated["auto_start_pipeline"])
        self.assertEqual(updated["width"], 1280)
        self.assertEqual(updated["contexts"], 3)


if __name__ == "__main__":
    unittest.main()
