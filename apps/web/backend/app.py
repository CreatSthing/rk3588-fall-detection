from __future__ import annotations

import asyncio
import json
import os
import random
import re
import signal
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .events import EventRepository


ROOT_DIR = Path(__file__).resolve().parents[3]
FRONTEND_DIR = ROOT_DIR / "apps" / "web" / "frontend"
DEFAULT_CONFIG_PATH = Path(__file__).with_name("config.example.json")
DEFAULT_CAMERA_ID = "cam1"


class StartRequest(BaseModel):
    source: Optional[str] = Field(default=None)
    contexts: Optional[int] = Field(default=None, ge=1, le=20)
    dry_run: bool = False


class CommandResponse(BaseModel):
    ok: bool
    message: str


class CameraUpsertRequest(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    name: str = ""
    source_url: str = Field(min_length=1)
    width: int = Field(default=640, ge=1, le=7680)
    height: int = Field(default=360, ge=1, le=4320)
    contexts: int = Field(default=8, ge=1, le=20)
    decoder: str = "software"


class FallEventRequest(BaseModel):
    id: Optional[str] = None
    event_type: str = "fall"
    state: str = Field(default="confirmed", pattern=r"^(confirmed|recovered)$")
    track_id: Optional[int] = None
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    timestamp: Optional[float] = None
    details: Dict[str, Any] = Field(default_factory=dict)
    video_path: Optional[str] = None
    recording_status: Optional[str] = Field(default=None, pattern=r"^(pending|recording|ready|failed)$")
    recording_error: Optional[str] = None


@dataclass
class CameraRuntime:
    camera_id: str
    running: bool = False
    recording: bool = False
    started_at: Optional[float] = None
    source: Optional[str] = None
    contexts: Optional[int] = None
    frames: int = 0
    fps: float = 0.0
    last_error: Optional[str] = None
    last_result: Optional[Dict[str, Any]] = None
    pipeline_process: Optional[asyncio.subprocess.Process] = None
    pipeline_task: Optional[asyncio.Task] = None
    simulator_task: Optional[asyncio.Task] = None
    record_process: Optional[asyncio.subprocess.Process] = None
    stream_process: Optional[asyncio.subprocess.Process] = None


@dataclass
class RuntimeState:
    cameras: Dict[str, CameraRuntime] = field(default_factory=dict)
    clients: Set[WebSocket] = field(default_factory=set)
    last_cpu_sample: Optional[Dict[str, int]] = None
    last_cpu_core_samples: Dict[str, Dict[str, int]] = field(default_factory=dict)
    last_domain_samples: Dict[str, Dict[str, int]] = field(default_factory=dict)
    event_record_tasks: Dict[str, asyncio.Task] = field(default_factory=dict)


state = RuntimeState()
_event_repositories: Dict[str, EventRepository] = {}
app = FastAPI(title="RK3588 Smart Camera Console", version="0.2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def load_config() -> Dict[str, Any]:
    config_path = Path(os.getenv("RK3588_WEB_CONFIG", str(DEFAULT_CONFIG_PATH)))
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        return json.load(f)


def config_path() -> Path:
    return Path(os.getenv("RK3588_WEB_CONFIG", str(DEFAULT_CONFIG_PATH)))


def save_config(config: Dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_path.replace(path)


def event_output_dir() -> Path:
    configured = os.getenv("RK3588_EVENT_DIR") or load_config().get("record_output_dir")
    if configured:
        return Path(str(configured))
    if os.name == "posix":
        return Path("/var/lib/rk3588-camera/events")
    return ROOT_DIR / ".runtime" / "events"


def event_repository() -> EventRepository:
    configured = os.getenv("RK3588_EVENT_DB")
    path = Path(configured) if configured else event_output_dir() / "events.db"
    key = str(path.resolve())
    if key not in _event_repositories:
        _event_repositories[key] = EventRepository(path)
    return _event_repositories[key]


def normalize_camera_config(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    cameras = config.get("cameras")
    if isinstance(cameras, list) and cameras:
        return cameras
    return [
        {
            "id": DEFAULT_CAMERA_ID,
            "name": "Camera 1",
            "width": int(config.get("video_width") or 640),
            "height": int(config.get("video_height") or 360),
            "player_url": config.get("player_url") or "/live/raw",
            "rtsp_url": config.get("rtsp_url") or "rtsp://127.0.0.1:8554/live/raw",
            "hls_url": config.get("hls_url") or "/live/raw/index.m3u8",
            "stream_command": config.get("stream_command") or [],
            "pipeline_command": config.get("pipeline_command") or [],
            "record_command": config.get("record_command") or [],
        }
    ]


def camera_configs() -> Dict[str, Dict[str, Any]]:
    configs: Dict[str, Dict[str, Any]] = {}
    for index, camera in enumerate(normalize_camera_config(load_config()), start=1):
        camera_id = str(camera.get("id") or f"cam{index}")
        normalized = dict(camera)
        normalized["id"] = camera_id
        normalized.setdefault("name", camera_id)
        normalized.setdefault("width", 640)
        normalized.setdefault("height", 360)
        normalized.setdefault("contexts", 1)
        normalized.setdefault("player_url", f"/live/{camera_id}")
        normalized.setdefault("rtsp_url", f"rtsp://127.0.0.1:8554/live/{camera_id}")
        normalized.setdefault("hls_url", f"/live/{camera_id}/index.m3u8")
        configs[camera_id] = normalized
    return configs


def make_camera_config(request: CameraUpsertRequest) -> Dict[str, Any]:
    camera_id = request.id
    mediamtx_path = f"/live/{camera_id}"
    return {
        "id": camera_id,
        "name": request.name or camera_id,
        "width": request.width,
        "height": request.height,
        "contexts": request.contexts,
        "source_url": request.source_url,
        "player_url": mediamtx_path,
        "rtsp_url": f"rtsp://127.0.0.1:8554{mediamtx_path}",
        "hls_url": f"{mediamtx_path}/index.m3u8",
        "stream_command": [
            "/opt/rk3588-camera/current/deploy/run_gst_mpp_stream.sh",
            request.source_url,
            f"rtsp://127.0.0.1:8554{mediamtx_path}",
        ],
        "pipeline_command": [
            "/opt/rk3588-camera/current/.venv/bin/python",
            "-m",
            "apps.fall_detection.main",
            "--model",
            "/opt/rk3588-camera/current/assets/weights/yolov8n-pose-fp16.rknn",
            "--source",
            "{source}",
            "--camera-id",
            "{camera_id}",
            "--decoder",
            "ffmpeg-software",
        ],
        "record_command": [],
    }


def get_camera_config(camera_id: str) -> Dict[str, Any]:
    configs = camera_configs()
    if camera_id not in configs:
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    return configs[camera_id]


def get_camera_state(camera_id: str) -> CameraRuntime:
    if camera_id not in state.cameras:
        state.cameras[camera_id] = CameraRuntime(camera_id=camera_id)
    return state.cameras[camera_id]


def build_pipeline_command(camera: Dict[str, Any], request: StartRequest) -> List[str]:
    command = list(camera.get("pipeline_command") or [])
    if not command:
        raise HTTPException(status_code=400, detail=f"pipeline_command is empty for camera {camera['id']}")
    source = str(request.source or camera.get("rtsp_url") or camera.get("source_url") or "")
    replacements = {
        "{source}": source,
        "{contexts}": str(request.contexts or camera.get("contexts") or 1),
        "{camera_id}": str(camera["id"]),
    }
    had_placeholders = any(str(item) in replacements for item in command)
    if had_placeholders:
        command = [replacements.get(str(item), str(item)) for item in command]
    elif request.source and len(command) >= 3:
        command[2] = request.source
    if request.contexts is not None:
        exe_name = Path(command[0]).name
        context_index = 3 if exe_name == "yolov5_thread_pool" else 4
        if not had_placeholders and len(command) > context_index:
            command[context_index] = str(request.contexts)
    stdbuf = shutil.which("stdbuf")
    if stdbuf:
        command = [stdbuf, "-oL", "-eL"] + command
    return command


def public_camera_state(camera_id: str) -> Dict[str, Any]:
    camera = get_camera_config(camera_id)
    runtime = get_camera_state(camera_id)
    return {
        "id": camera_id,
        "name": camera.get("name", camera_id),
        "width": int(camera.get("width") or 640),
        "height": int(camera.get("height") or 360),
        "player_url": camera.get("player_url"),
        "rtsp_url": camera.get("rtsp_url"),
        "hls_url": camera.get("hls_url"),
        "running": runtime.running,
        "recording": runtime.recording,
        "started_at": runtime.started_at,
        "uptime_sec": round(time.time() - runtime.started_at, 3) if runtime.started_at else 0,
        "source": runtime.source,
        "contexts": runtime.contexts,
        "frames": runtime.frames,
        "fps": round(runtime.fps, 2),
        "last_error": runtime.last_error,
        "last_result": runtime.last_result,
        "streaming": runtime.stream_process is not None and runtime.stream_process.returncode is None,
    }


def public_state() -> Dict[str, Any]:
    configs = camera_configs()
    cameras = [public_camera_state(camera_id) for camera_id in configs]
    primary = cameras[0] if cameras else {}
    return {**primary, "cameras": cameras}


async def broadcast(event: Dict[str, Any]) -> None:
    dead_clients: List[WebSocket] = []
    for websocket in list(state.clients):
        try:
            await websocket.send_json(event)
        except Exception:
            dead_clients.append(websocket)
    for websocket in dead_clients:
        state.clients.discard(websocket)


def _safe_filename(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:80] or "event"


def build_event_record_command(camera: Dict[str, Any], output_path: Path, duration: int) -> List[str]:
    source = str(camera.get("event_record_source") or camera.get("rtsp_url") or camera.get("source_url") or "")
    configured = list(camera.get("event_record_command") or [])
    if configured:
        replacements = {
            "{source}": source,
            "{output}": str(output_path),
            "{duration}": str(duration),
        }
        return [replacements.get(str(item), str(item)) for item in configured]
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg not found and event_record_command is not configured")
    if not source:
        raise RuntimeError("camera has no rtsp_url/source_url for event recording")
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    if source.lower().startswith("rtsp://"):
        command += ["-rtsp_transport", "tcp"]
    command += [
        "-i", source,
        "-t", str(duration),
        "-map", "0:v:0",
        "-an",
        "-c:v", "copy",
        "-movflags", "+faststart",
        str(output_path),
    ]
    return command


async def record_fall_event(event_id: str, camera_id: str) -> None:
    repository = event_repository()
    config = load_config()
    duration = max(3, min(int(config.get("event_record_seconds") or 20), 300))
    output_dir = event_output_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    happened_at = time.strftime("%Y%m%d-%H%M%S", time.localtime())
    output_path = output_dir / f"{happened_at}-{_safe_filename(camera_id)}-{_safe_filename(event_id)}.mp4"
    try:
        camera = get_camera_config(camera_id)
        command = build_event_record_command(camera, output_path, duration)
        updated = repository.set_recording(event_id, "recording", str(output_path))
        await broadcast({"type": "alarm_update", "camera_id": camera_id, "payload": updated})
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(ROOT_DIR),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            message = stderr.decode("utf-8", errors="replace").strip()[-1000:]
            raise RuntimeError(message or f"event recorder exited with code {process.returncode}")
        updated = repository.set_recording(event_id, "ready", str(output_path))
        await broadcast({"type": "alarm_update", "camera_id": camera_id, "payload": updated})
    except Exception as exc:
        try:
            updated = repository.set_recording(event_id, "failed", str(output_path), str(exc))
            await broadcast({"type": "alarm_update", "camera_id": camera_id, "payload": updated})
        except KeyError:
            pass
        await broadcast({
            "type": "log",
            "camera_id": camera_id,
            "payload": {"level": "error", "message": f"fall recording failed: {exc}"},
        })
    finally:
        state.event_record_tasks.pop(event_id, None)


async def process_fall_event(camera_id: str, raw_event: Dict[str, Any]) -> Dict[str, Any]:
    if str(raw_event.get("event_type") or "fall") != "fall":
        raise ValueError("only fall events are supported")
    event = dict(raw_event)
    event["id"] = str(event.get("id") or uuid.uuid4().hex)
    event["camera_id"] = camera_id
    event["timestamp"] = float(event.get("timestamp") or time.time())
    event["state"] = str(event.get("state") or "confirmed")
    existing = event_repository().get(event["id"])
    supplied_video = event.get("video_path")
    supplied_status = event.get("recording_status")
    # A late "recording ready" update can arrive after the person recovered.
    # It must update only clip metadata: re-upserting it would also reset the
    # recovered timestamp to the original fall timestamp.
    if existing is not None and supplied_status:
        saved = existing
    else:
        saved = event_repository().upsert(event)

    if supplied_video and supplied_status in {"recording", "ready", "failed"}:
        saved = event_repository().set_recording(
            event["id"], str(supplied_status), str(supplied_video), event.get("recording_error")
        )

    if event["state"] == "confirmed" and existing is None:
        await broadcast({"type": "alarm", "camera_id": camera_id, "payload": saved})
        recording_enabled = bool(load_config().get("event_recording_enabled", True))
        if supplied_video:
            await broadcast({"type": "alarm_update", "camera_id": camera_id, "payload": saved})
        elif recording_enabled and event["id"] not in state.event_record_tasks:
            task = asyncio.create_task(record_fall_event(event["id"], camera_id))
            state.event_record_tasks[event["id"]] = task
    elif event["state"] == "recovered" or supplied_status:
        await broadcast({"type": "alarm_update", "camera_id": camera_id, "payload": saved})
    return saved


async def process_payload_events(camera_id: str, payload: Dict[str, Any]) -> None:
    events = payload.get("events") or []
    if not isinstance(events, list):
        return
    for item in events:
        if not isinstance(item, dict):
            continue
        try:
            await process_fall_event(camera_id, item)
        except (TypeError, ValueError) as exc:
            await broadcast({
                "type": "log",
                "camera_id": camera_id,
                "payload": {"level": "warning", "message": f"invalid fall event: {exc}"},
            })


def parse_pipeline_line(line: str) -> Optional[Dict[str, Any]]:
    line = line.strip()
    if not line:
        return None

    if line.startswith("{") and line.endswith("}"):
        try:
            payload = json.loads(line)
            if isinstance(payload, dict):
                return {"type": "detection", "payload": payload}
        except json.JSONDecodeError:
            pass

    lowered = line.lower()
    if (
        "static shape" in lowered
        and ("query dynamic range failed" in lowered or "rknn_query_input_dynamic_range" in lowered)
    ):
        return {"type": "log", "payload": {"level": "warning", "message": line}}
    if (
        "error while decoding" in lowered
        or "concealing" in lowered
        or (lowered.startswith("[h264") and "error" in lowered)
        or (lowered.startswith("[hevc") and "error" in lowered)
    ):
        return {"type": "log", "payload": {"level": "warning", "message": line}}
    if "fps" in lowered:
        return {"type": "log", "payload": {"level": "info", "message": line}}
    if "error" in lowered or "failed" in lowered:
        return {"type": "log", "payload": {"level": "error", "message": line}}
    return {"type": "log", "payload": {"level": "debug", "message": line}}


async def read_pipeline_output(camera_id: str, process: asyncio.subprocess.Process) -> None:
    assert process.stdout is not None
    runtime = get_camera_state(camera_id)
    log_dir = Path("/var/log/rk3588-camera")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"pipeline-{camera_id}.log"
    while True:
        raw = await process.stdout.readline()
        if not raw:
            break
        line = raw.decode("utf-8", errors="replace")
        event = parse_pipeline_line(line)
        log_line = line
        if event is not None and event["type"] == "detection" and event["payload"].get("preview_jpeg"):
            log_payload = dict(event["payload"])
            log_payload["preview_jpeg"] = f"<omitted:{len(log_payload['preview_jpeg'])} base64 chars>"
            log_line = json.dumps(log_payload, ensure_ascii=False, separators=(",", ":")) + "\n"
        try:
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(log_line)
        except OSError:
            pass
        if event is None:
            continue
        event["camera_id"] = camera_id
        if event["type"] == "detection":
            runtime.frames += 1
            payload = dict(event["payload"])
            payload["camera_id"] = camera_id
            runtime.last_result = {key: value for key, value in payload.items() if key != "preview_jpeg"}
            event["payload"] = payload
            if "fps" in payload:
                try:
                    runtime.fps = float(payload["fps"])
                except (TypeError, ValueError):
                    pass
            await process_payload_events(camera_id, payload)
        elif event["payload"]["level"] == "error":
            runtime.last_error = event["payload"]["message"]
        await broadcast(event)

    return_code = await process.wait()
    runtime.running = False
    runtime.pipeline_process = None
    await broadcast({"type": "status", "payload": public_state()})
    await broadcast({
        "type": "log",
        "camera_id": camera_id,
        "payload": {"level": "info", "message": f"{camera_id} pipeline exited with code {return_code}"},
    })


async def simulate_detections(camera_id: str) -> None:
    runtime = get_camera_state(camera_id)
    camera = get_camera_config(camera_id)
    width = int(camera.get("width") or 640)
    height = int(camera.get("height") or 360)
    labels = ["person", "car", "bicycle", "dog"]
    last_time = time.time()
    while runtime.running:
        await asyncio.sleep(0.25)
        now = time.time()
        runtime.frames += 1
        runtime.fps = 1.0 / max(now - last_time, 1e-6)
        last_time = now
        detections = [
            {
                "label": random.choice(labels),
                "score": round(random.uniform(0.55, 0.95), 3),
                "box": {
                    "x": random.randint(20, max(21, width - 160)),
                    "y": random.randint(20, max(21, height - 160)),
                    "w": random.randint(60, min(180, max(61, width // 3))),
                    "h": random.randint(60, min(180, max(61, height // 2))),
                },
            }
            for _ in range(random.randint(1, 4))
        ]
        result = {
            "camera_id": camera_id,
            "frame_id": runtime.frames,
            "timestamp": now,
            "fps": round(runtime.fps, 2),
            "detections": detections,
            "events": [],
        }
        if runtime.frames % 40 == 12:
            event_id = uuid.uuid4().hex
            detections[0]["action"] = "fall"
            detections[0]["track_id"] = 1
            result["events"] = [{
                "id": event_id,
                "event_type": "fall",
                "state": "confirmed",
                "track_id": 1,
                "confidence": 0.91,
                "timestamp": now,
                "details": {"simulation": True},
            }]
        runtime.last_result = result
        await broadcast({"type": "detection", "camera_id": camera_id, "payload": result})
        await process_payload_events(camera_id, result)
        await broadcast({"type": "status", "payload": public_state()})


async def stop_process(process: Optional[asyncio.subprocess.Process]) -> None:
    if process is None or process.returncode is not None:
        return
    try:
        process.send_signal(signal.SIGTERM)
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


def read_text(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


def read_text_timeout(path: str, timeout_sec: float = 0.25) -> Optional[str]:
    try:
        result = subprocess.run(
            ["cat", path],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def read_int(path: str) -> Optional[int]:
    text = read_text(path)
    if text is None:
        return None
    match = re.search(r"-?\d+", text)
    return int(match.group(0)) if match else None


def parse_cpu_stat() -> Optional[Dict[str, int]]:
    text = read_text("/proc/stat")
    if not text:
        return None
    first = text.splitlines()[0].split()
    if not first or first[0] != "cpu":
        return None
    values = [int(value) for value in first[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    total = sum(values)
    return {"total": total, "idle": idle}


def parse_cpu_line(line: str) -> Optional[Dict[str, int]]:
    parts = line.split()
    if not parts or not re.fullmatch(r"cpu\d+", parts[0]):
        return None
    values = [int(value) for value in parts[1:]]
    idle = values[3] + (values[4] if len(values) > 4 else 0)
    return {"id": parts[0], "total": sum(values), "idle": idle}


def cpu_usage_percent() -> Optional[float]:
    current = parse_cpu_stat()
    previous = state.last_cpu_sample
    state.last_cpu_sample = current
    if not current or not previous:
        return None
    total_delta = current["total"] - previous["total"]
    idle_delta = current["idle"] - previous["idle"]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 1)


def cpu_core_metrics() -> List[Dict[str, Any]]:
    text = read_text("/proc/stat") or ""
    cores: List[Dict[str, Any]] = []
    for line in text.splitlines():
        parsed = parse_cpu_line(line)
        if not parsed:
            continue
        core_id = parsed["id"]
        previous = state.last_cpu_core_samples.get(core_id)
        usage = None
        if previous:
            total_delta = parsed["total"] - previous["total"]
            idle_delta = parsed["idle"] - previous["idle"]
            if total_delta > 0:
                usage = round(max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0)), 1)
        state.last_cpu_core_samples[core_id] = parsed
        cores.append({"id": core_id, "usage_percent": usage})
    return cores


def memory_metrics() -> Dict[str, Any]:
    values: Dict[str, int] = {}
    text = read_text("/proc/meminfo") or ""
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            values[parts[0].rstrip(":")] = int(parts[1])
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    used = total - available if total is not None and available is not None else None
    percent = round(used / total * 100.0, 1) if total and used is not None else None
    return {
        "total_mb": round(total / 1024, 1) if total else None,
        "available_mb": round(available / 1024, 1) if available else None,
        "used_mb": round(used / 1024, 1) if used is not None else None,
        "used_percent": percent,
    }


def temperature_metrics() -> List[Dict[str, Any]]:
    temps: List[Dict[str, Any]] = []
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        name = read_text(str(zone / "type"))
        raw_temp = read_int(str(zone / "temp"))
        if not name or raw_temp is None:
            continue
        temps.append({"name": name, "temp_c": round(raw_temp / 1000.0, 1)})
    return temps


def parse_load_percent(text: Optional[str]) -> Optional[float]:
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    value = float(match.group(1))
    return round(max(0.0, min(100.0, value)), 1)


def parse_npu_cores(raw_load: Optional[str]) -> List[Dict[str, Any]]:
    if not raw_load:
        return []
    cores: List[Dict[str, Any]] = []
    for match in re.finditer(r"Core(\d+)\s*:\s*(\d+(?:\.\d+)?)\s*%", raw_load):
        cores.append({
            "name": f"Core{match.group(1)}",
            "load_percent": round(float(match.group(2)), 1),
        })
    return cores


def parse_rga_schedulers(raw_load: Optional[str]) -> List[Dict[str, Any]]:
    if not raw_load:
        return []
    schedulers: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in raw_load.splitlines():
        scheduler_match = re.search(r"scheduler\[(\d+)\]\s*:\s*(\S+)", line)
        if scheduler_match:
            current = {"id": int(scheduler_match.group(1)), "name": scheduler_match.group(2), "load_percent": None}
            schedulers.append(current)
            continue
        load_match = re.search(r"load\s*=\s*(\d+(?:\.\d+)?)\s*%", line)
        if load_match and current is not None:
            current["load_percent"] = round(float(load_match.group(1)), 1)
    return schedulers


def devfreq_metric(name_hint: str) -> Optional[Dict[str, Any]]:
    for node in Path("/sys/class/devfreq").glob("*"):
        node_name = (read_text(str(node / "name")) or node.name).lower()
        if name_hint not in node_name and name_hint not in node.name.lower():
            continue
        cur_freq = read_int(str(node / "cur_freq"))
        load = parse_load_percent(read_text(str(node / "load")))
        return {
            "name": node_name,
            "load_percent": load,
            "freq_mhz": round(cur_freq / 1_000_000, 1) if cur_freq else None,
            "available": True,
        }
    return None


def debug_npu_metric() -> Dict[str, Any]:
    metric = devfreq_metric("npu") or {"name": "npu", "available": False}
    raw_load = read_text_timeout("/sys/kernel/debug/rknpu/load")
    cores = parse_npu_cores(raw_load)
    debug_load = max((core["load_percent"] for core in cores), default=None)
    debug_freq = read_int("/sys/kernel/debug/rknpu/freq")
    debug_power = read_text("/sys/kernel/debug/rknpu/power")
    temp = next((item["temp_c"] for item in temperature_metrics() if "npu" in item["name"].lower()), None)
    if debug_load is not None:
        metric["load_percent"] = debug_load
        metric["available"] = True
    if debug_freq is not None:
        metric["freq_mhz"] = round(debug_freq / 1_000_000, 1) if debug_freq > 100000 else debug_freq
    if debug_power is not None:
        metric["power"] = debug_power
    metric["raw_load"] = raw_load
    metric["cores"] = cores
    metric["temp_c"] = temp
    return metric


def debug_rga_metric() -> Dict[str, Any]:
    raw_load = read_text_timeout("/sys/kernel/debug/rkrga/load")
    schedulers = parse_rga_schedulers(raw_load)
    load_percent = max((item["load_percent"] for item in schedulers if item["load_percent"] is not None), default=None)
    return {
        "available": raw_load is not None,
        "load_percent": load_percent,
        "schedulers": schedulers,
        "raw_load": raw_load,
    }


def domain_busy_percent(domain: str) -> Dict[str, Any]:
    active = read_int(f"/sys/kernel/debug/pm_genpd/{domain}/active_time")
    idle = read_int(f"/sys/kernel/debug/pm_genpd/{domain}/total_idle_time")
    if active is None or idle is None:
        return {"name": domain, "available": False, "busy_percent": None}
    current = {"active": active, "idle": idle}
    previous = state.last_domain_samples.get(domain)
    state.last_domain_samples[domain] = current
    busy = None
    if previous:
        active_delta = active - previous["active"]
        idle_delta = idle - previous["idle"]
        total_delta = active_delta + idle_delta
        if total_delta > 0:
            busy = round(max(0.0, min(100.0, active_delta / total_delta * 100.0)), 1)
    return {
        "name": domain,
        "available": True,
        "busy_percent": busy,
        "active_time": active,
        "idle_time": idle,
    }


def clock_metric(clock_name: str) -> Dict[str, Any]:
    rate = read_int(f"/sys/kernel/debug/clk/{clock_name}/clk_rate")
    enabled = read_int(f"/sys/kernel/debug/clk/{clock_name}/clk_enable_count")
    return {
        "name": clock_name,
        "available": rate is not None or enabled is not None,
        "freq_mhz": round(rate / 1_000_000, 1) if rate else None,
        "enable_count": enabled,
    }


def system_metrics() -> Dict[str, Any]:
    temps = temperature_metrics()
    return {
        "timestamp": time.time(),
        "cpu": {"usage_percent": cpu_usage_percent(), "cores": cpu_core_metrics()},
        "memory": memory_metrics(),
        "temperatures": temps,
        "npu": debug_npu_metric(),
        "mpp": {
            "decoder": [domain_busy_percent("rkvdec0"), domain_busy_percent("rkvdec1")],
            "encoder": [domain_busy_percent("venc0"), domain_busy_percent("venc1")],
        },
        "rga": {
            "debug": debug_rga_metric(),
            "domains": [domain_busy_percent("rga30"), domain_busy_percent("rga31")],
            "clocks": [clock_metric("clk_rga3_0_core"), clock_metric("clk_rga3_1_core")],
        },
    }


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/status")
async def get_status() -> Dict[str, Any]:
    return public_state()


@app.get("/api/cameras")
async def get_cameras() -> Dict[str, Any]:
    return {"cameras": public_state()["cameras"]}


@app.get("/api/system/metrics")
async def get_system_metrics() -> Dict[str, Any]:
    return system_metrics()


@app.get("/api/events")
async def get_events(limit: int = 100, camera_id: Optional[str] = None) -> Dict[str, Any]:
    return {"events": event_repository().list(limit=limit, camera_id=camera_id)}


@app.post("/api/cameras/{camera_id}/fall-events")
async def ingest_fall_event(camera_id: str, request: FallEventRequest) -> Dict[str, Any]:
    get_camera_config(camera_id)
    payload = request.dict()
    payload["id"] = payload.get("id") or uuid.uuid4().hex
    return {"ok": True, "event": await process_fall_event(camera_id, payload)}


@app.post("/api/events/{event_id}/acknowledge")
async def acknowledge_event(event_id: str) -> Dict[str, Any]:
    try:
        event = event_repository().acknowledge(event_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"event not found: {event_id}") from exc
    await broadcast({"type": "alarm_update", "camera_id": event["camera_id"], "payload": event})
    return {"ok": True, "event": event}


@app.get("/api/events/{event_id}/video")
async def get_event_video(event_id: str) -> FileResponse:
    event = event_repository().get(event_id)
    if event is None:
        raise HTTPException(status_code=404, detail=f"event not found: {event_id}")
    if not event.get("video_ready") or not event.get("video_path"):
        raise HTTPException(status_code=409, detail="event video is not ready")
    path = Path(str(event["video_path"])).resolve()
    allowed = event_output_dir().resolve()
    try:
        if os.path.commonpath([str(path), str(allowed)]) != str(allowed):
            raise HTTPException(status_code=403, detail="event video path is outside the recording directory")
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="invalid event video path") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="event video file is missing")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.post("/api/cameras", response_model=CommandResponse)
async def add_camera(request: CameraUpsertRequest) -> CommandResponse:
    config = load_config()
    cameras = list(normalize_camera_config(config))
    if any(str(camera.get("id")) == request.id for camera in cameras):
        raise HTTPException(status_code=409, detail=f"camera already exists: {request.id}")
    cameras.append(make_camera_config(request))
    config["cameras"] = cameras
    save_config(config)
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message=f"camera added: {request.id}")


@app.put("/api/cameras/{camera_id}", response_model=CommandResponse)
async def update_camera(camera_id: str, request: CameraUpsertRequest) -> CommandResponse:
    if request.id != camera_id:
        raise HTTPException(status_code=400, detail="camera id in path and body must match")
    config = load_config()
    cameras = list(normalize_camera_config(config))
    for index, camera in enumerate(cameras):
        if str(camera.get("id")) == camera_id:
            cameras[index] = make_camera_config(request)
            config["cameras"] = cameras
            save_config(config)
            await broadcast({"type": "status", "payload": public_state()})
            return CommandResponse(ok=True, message=f"camera updated: {camera_id}")
    raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")


@app.delete("/api/cameras/{camera_id}", response_model=CommandResponse)
async def remove_camera(camera_id: str) -> CommandResponse:
    runtime = get_camera_state(camera_id)
    streaming = runtime.stream_process is not None and runtime.stream_process.returncode is None
    if runtime.running or streaming or runtime.recording:
        raise HTTPException(status_code=409, detail="stop stream/pipeline/recording before removing camera")
    config = load_config()
    cameras = list(normalize_camera_config(config))
    kept = [camera for camera in cameras if str(camera.get("id")) != camera_id]
    if len(kept) == len(cameras):
        raise HTTPException(status_code=404, detail=f"camera not found: {camera_id}")
    config["cameras"] = kept
    save_config(config)
    state.cameras.pop(camera_id, None)
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message=f"camera removed: {camera_id}")


@app.post("/api/cameras/{camera_id}/pipeline/start", response_model=CommandResponse)
@app.put("/api/cameras/{camera_id}/pipeline", response_model=CommandResponse)
async def start_camera_pipeline(camera_id: str, request: StartRequest) -> CommandResponse:
    camera = get_camera_config(camera_id)
    runtime = get_camera_state(camera_id)
    if runtime.running:
        raise HTTPException(status_code=409, detail="pipeline is already running")

    runtime.running = True
    runtime.started_at = time.time()
    runtime.source = request.source
    runtime.contexts = request.contexts
    runtime.frames = 0
    runtime.fps = 0.0
    runtime.last_error = None
    runtime.last_result = None

    if request.dry_run:
        runtime.simulator_task = asyncio.create_task(simulate_detections(camera_id))
        await broadcast({"type": "status", "payload": public_state()})
        return CommandResponse(ok=True, message=f"{camera_id} simulated pipeline started")

    command = build_pipeline_command(camera, request)
    try:
        runtime.pipeline_process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(ROOT_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        runtime.pipeline_task = asyncio.create_task(read_pipeline_output(camera_id, runtime.pipeline_process))
    except Exception as exc:
        runtime.running = False
        runtime.last_error = str(exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message=f"{camera_id} pipeline started")


@app.post("/api/cameras/{camera_id}/pipeline/stop", response_model=CommandResponse)
@app.delete("/api/cameras/{camera_id}/pipeline", response_model=CommandResponse)
async def stop_camera_pipeline(camera_id: str) -> CommandResponse:
    get_camera_config(camera_id)
    runtime = get_camera_state(camera_id)
    if runtime.simulator_task:
        runtime.running = False
        runtime.simulator_task.cancel()
        runtime.simulator_task = None
    await stop_process(runtime.pipeline_process)
    runtime.running = False
    runtime.pipeline_process = None
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message=f"{camera_id} pipeline stopped")


@app.post("/api/cameras/{camera_id}/stream/start", response_model=CommandResponse)
@app.put("/api/cameras/{camera_id}/stream", response_model=CommandResponse)
async def start_camera_stream(camera_id: str) -> CommandResponse:
    camera = get_camera_config(camera_id)
    runtime = get_camera_state(camera_id)
    if runtime.stream_process is not None and runtime.stream_process.returncode is None:
        raise HTTPException(status_code=409, detail="stream is already running")
    command = list(camera.get("stream_command") or [])
    if not command:
        raise HTTPException(status_code=400, detail=f"stream_command is empty for camera {camera_id}")
    try:
        runtime.stream_process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(ROOT_DIR),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except Exception as exc:
        runtime.last_error = str(exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message=f"{camera_id} stream started")


@app.post("/api/cameras/{camera_id}/stream/stop", response_model=CommandResponse)
@app.delete("/api/cameras/{camera_id}/stream", response_model=CommandResponse)
async def stop_camera_stream(camera_id: str) -> CommandResponse:
    get_camera_config(camera_id)
    runtime = get_camera_state(camera_id)
    await stop_process(runtime.stream_process)
    runtime.stream_process = None
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message=f"{camera_id} stream stopped")


@app.post("/api/cameras/{camera_id}/recording/start", response_model=CommandResponse)
@app.put("/api/cameras/{camera_id}/recording", response_model=CommandResponse)
async def start_camera_recording(camera_id: str) -> CommandResponse:
    camera = get_camera_config(camera_id)
    runtime = get_camera_state(camera_id)
    if runtime.recording:
        raise HTTPException(status_code=409, detail="recording is already running")

    command = list(camera.get("record_command") or [])
    if command:
        try:
            runtime.record_process = await asyncio.create_subprocess_exec(*command, cwd=str(ROOT_DIR))
        except Exception as exc:
            runtime.last_error = str(exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    runtime.recording = True
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message=f"{camera_id} recording started")


@app.post("/api/cameras/{camera_id}/recording/stop", response_model=CommandResponse)
@app.delete("/api/cameras/{camera_id}/recording", response_model=CommandResponse)
async def stop_camera_recording(camera_id: str) -> CommandResponse:
    get_camera_config(camera_id)
    runtime = get_camera_state(camera_id)
    await stop_process(runtime.record_process)
    runtime.record_process = None
    runtime.recording = False
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message=f"{camera_id} recording stopped")


@app.post("/api/pipeline/start", response_model=CommandResponse)
@app.put("/api/pipeline", response_model=CommandResponse)
async def start_pipeline(request: StartRequest) -> CommandResponse:
    return await start_camera_pipeline(DEFAULT_CAMERA_ID, request)


@app.post("/api/pipeline/stop", response_model=CommandResponse)
@app.delete("/api/pipeline", response_model=CommandResponse)
async def stop_pipeline() -> CommandResponse:
    return await stop_camera_pipeline(DEFAULT_CAMERA_ID)


@app.post("/api/stream/start", response_model=CommandResponse)
@app.put("/api/stream", response_model=CommandResponse)
async def start_stream() -> CommandResponse:
    return await start_camera_stream(DEFAULT_CAMERA_ID)


@app.post("/api/stream/stop", response_model=CommandResponse)
@app.delete("/api/stream", response_model=CommandResponse)
async def stop_stream() -> CommandResponse:
    return await stop_camera_stream(DEFAULT_CAMERA_ID)


@app.post("/api/recording/start", response_model=CommandResponse)
@app.put("/api/recording", response_model=CommandResponse)
async def start_recording() -> CommandResponse:
    return await start_camera_recording(DEFAULT_CAMERA_ID)


@app.post("/api/recording/stop", response_model=CommandResponse)
@app.delete("/api/recording", response_model=CommandResponse)
async def stop_recording() -> CommandResponse:
    return await stop_camera_recording(DEFAULT_CAMERA_ID)


@app.websocket("/ws/detections")
async def detections_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    state.clients.add(websocket)
    await websocket.send_json({"type": "status", "payload": public_state()})
    await websocket.send_json({"type": "alarm_history", "payload": {"events": event_repository().list(limit=50)}})
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.clients.discard(websocket)


@app.on_event("startup")
async def autostart_configured_cameras() -> None:
    cameras = camera_configs()
    stream_started = False
    for camera_id, camera in cameras.items():
        if not camera.get("auto_start_stream"):
            continue
        try:
            await start_camera_stream(camera_id)
            stream_started = True
        except Exception as exc:
            get_camera_state(camera_id).last_error = f"stream autostart failed: {exc}"
    if stream_started:
        await asyncio.sleep(2.0)
    for camera_id, camera in cameras.items():
        if not camera.get("auto_start_pipeline"):
            continue
        try:
            await start_camera_pipeline(camera_id, StartRequest())
        except Exception as exc:
            runtime = get_camera_state(camera_id)
            runtime.running = False
            runtime.last_error = f"pipeline autostart failed: {exc}"


@app.on_event("shutdown")
async def stop_camera_processes() -> None:
    for runtime in list(state.cameras.values()):
        runtime.running = False
        if runtime.simulator_task:
            runtime.simulator_task.cancel()
        await stop_process(runtime.pipeline_process)
        await stop_process(runtime.record_process)
        await stop_process(runtime.stream_process)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
