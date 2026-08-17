from __future__ import annotations

import asyncio
import json
import os
import random
import signal
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field


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


state = RuntimeState()
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
        normalized.setdefault("player_url", f"/live/{camera_id}")
        normalized.setdefault("rtsp_url", f"rtsp://127.0.0.1:8554/live/{camera_id}")
        normalized.setdefault("hls_url", f"/live/{camera_id}/index.m3u8")
        configs[camera_id] = normalized
    return configs


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
    if request.source and len(command) >= 3:
        command[2] = request.source
    if request.contexts is not None:
        exe_name = Path(command[0]).name
        context_index = 3 if exe_name == "yolov5_thread_pool" else 4
        if len(command) > context_index:
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
        try:
            with log_path.open("a", encoding="utf-8") as log_file:
                log_file.write(line)
        except OSError:
            pass
        event = parse_pipeline_line(line)
        if event is None:
            continue
        event["camera_id"] = camera_id
        if event["type"] == "detection":
            runtime.frames += 1
            payload = dict(event["payload"])
            payload["camera_id"] = camera_id
            runtime.last_result = payload
            event["payload"] = payload
            if "fps" in payload:
                try:
                    runtime.fps = float(payload["fps"])
                except (TypeError, ValueError):
                    pass
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
        }
        runtime.last_result = result
        await broadcast({"type": "detection", "camera_id": camera_id, "payload": result})
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


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(FRONTEND_DIR / "index.html")


@app.get("/api/status")
async def get_status() -> Dict[str, Any]:
    return public_state()


@app.get("/api/cameras")
async def get_cameras() -> Dict[str, Any]:
    return {"cameras": public_state()["cameras"]}


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
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.clients.discard(websocket)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
