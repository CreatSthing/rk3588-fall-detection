import unittest

from deploy.rewrite_config_paths import rewrite


class RewriteConfigPathsTests(unittest.TestCase):
    def test_systemd_and_docker_paths_are_reversible(self):
        root = "/opt/rk3588-camera/current"
        systemd_config = {
            "stream_command": [root + "/deploy/run_gst_mpp_stream.sh"],
            "pipeline_command": [
                root + "/.venv/bin/python",
                "--model",
                root + "/assets/weights/model.rknn",
            ],
        }

        docker_config = rewrite(systemd_config, "docker", root, {root})
        self.assertEqual(docker_config["stream_command"][0], "/app/deploy/run_gst_mpp_stream.sh")
        self.assertEqual(docker_config["pipeline_command"][0], "python3")
        self.assertEqual(docker_config["pipeline_command"][2], "/app/assets/weights/model.rknn")

        restored = rewrite(docker_config, "systemd", root, {root})
        self.assertEqual(restored, systemd_config)


if __name__ == "__main__":
    unittest.main()
