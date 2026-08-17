from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from fractions import Fraction
from typing import Optional, Union

import numpy as np


def is_network_source(source: Union[str, int]) -> bool:
    return isinstance(source, str) and source.lower().startswith(("rtsp://", "rtmp://", "http://", "https://"))


@dataclass(frozen=True)
class VideoMetadata:
    width: int
    height: int
    fps: float
    codec: str


def _parse_fps(value: str, fallback: float) -> float:
    try:
        fps = float(Fraction(value))
    except (ValueError, ZeroDivisionError):
        return fallback
    return fps if 0 < fps <= 120 else fallback


def probe_video(source: str, fallback_fps: float = 15.0) -> VideoMetadata:
    command = ["ffprobe", "-v", "error"]
    if source.lower().startswith("rtsp://"):
        command += ["-rtsp_transport", "tcp"]
    command += [
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height,avg_frame_rate,codec_name",
        "-of", "json",
        source,
    ]
    completed = subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)
    streams = json.loads(completed.stdout).get("streams") or []
    if not streams:
        raise RuntimeError(f"ffprobe found no video stream: {source}")
    stream = streams[0]
    width = int(stream.get("width") or 0)
    height = int(stream.get("height") or 0)
    if width <= 0 or height <= 0:
        raise RuntimeError(f"ffprobe returned invalid dimensions: {width}x{height}")
    return VideoMetadata(
        width=width,
        height=height,
        fps=_parse_fps(str(stream.get("avg_frame_rate") or ""), fallback_fps),
        codec=str(stream.get("codec_name") or ""),
    )


class OpenCVVideoSource:
    def __init__(self, source: Union[str, int], fallback_fps: float):
        import cv2

        self.cv2 = cv2
        self.capture = cv2.VideoCapture(source)
        if not self.capture.isOpened():
            raise RuntimeError(f"cannot open video source: {source}")
        fps = float(self.capture.get(self.cv2.CAP_PROP_FPS))
        self.fps = fps if 0 < fps <= 60 else fallback_fps

    def read(self):
        return self.capture.read()

    def close(self) -> None:
        self.capture.release()


class FFmpegSoftwareVideoSource:
    """Decode network streams with FFmpeg's software decoder.

    The Orange Pi image auto-selects its h264_rkmpp decoder in some OpenCV/FFmpeg
    paths. That decoder currently fails RGA stride conversion for the camera's
    640x360 stream. Explicitly selecting the codec's software decoder avoids the
    broken hardware conversion while the NPU remains responsible for inference.
    """

    def __init__(self, source: str, fallback_fps: float):
        metadata = probe_video(source, fallback_fps)
        self.width = metadata.width
        self.height = metadata.height
        self.fps = metadata.fps
        self.frame_bytes = self.width * self.height * 3
        command = ["ffmpeg", "-hide_banner", "-loglevel", "warning"]
        if source.lower().startswith("rtsp://"):
            command += ["-rtsp_transport", "tcp"]
        if metadata.codec:
            command += ["-c:v", metadata.codec]
        command += ["-i", source, "-map", "0:v:0", "-an", "-sn", "-dn", "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
        self.process = subprocess.Popen(command, stdout=subprocess.PIPE)
        if self.process.stdout is None:
            self.process.kill()
            raise RuntimeError("ffmpeg stdout pipe was not created")

    def read(self):
        chunks = bytearray()
        while len(chunks) < self.frame_bytes:
            data = self.process.stdout.read(self.frame_bytes - len(chunks))
            if not data:
                return False, None
            chunks.extend(data)
        frame = np.frombuffer(chunks, dtype=np.uint8).reshape(self.height, self.width, 3)
        return True, frame

    def close(self) -> None:
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.process.stdout is not None:
            self.process.stdout.close()


def open_video_source(source: Union[str, int], decoder: str, fallback_fps: float):
    if decoder == "ffmpeg-software" or (decoder == "auto" and is_network_source(source)):
        if not isinstance(source, str):
            raise ValueError("ffmpeg-software decoder requires a URL or file path")
        return FFmpegSoftwareVideoSource(source, fallback_fps)
    return OpenCVVideoSource(source, fallback_fps)
