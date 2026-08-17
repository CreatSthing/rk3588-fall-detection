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


class StartRequest(BaseModel):
    source: Optional[str] = Field(
        default=None,
        description="Optional video path or stream URL. If provided, it replaces the third argument in pipeline_command.",
    )
    contexts: Optional[int] = Field(default=None, ge=1, le=20)
    dry_run: bool = Field(
        default=False,
        description="Run a simulated pipeline that emits fake detection events for UI testing.",
    )


class CommandResponse(BaseModel):
    ok: bool
    message: str


@dataclass
class RuntimeState:
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
    clients: Set[WebSocket] = field(default_factory=set)


state = RuntimeState()
app = FastAPI(title="RK3588 Smart Camera Console", version="0.1.0")

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


def build_pipeline_command(request: StartRequest) -> List[str]:
    config = load_config()
    command = list(config.get("pipeline_command") or [])
    if not command:
        raise HTTPException(status_code=400, detail="pipeline_command is empty. Set RK3588_WEB_CONFIG or edit config.example.json.")
    if request.source and len(command) >= 3:
        command[2] = request.source
    if request.contexts is not None:
        exe_name = Path(command[0]).name
        # yolov5_thread_pool 参数顺序:
        #   <model> <video> [contexts] [draw] [decoder] [profile.csv] [json_events]
        # mpp_rga_thread_pool 参数顺序:
        #   <model> <annexb> <codec> [contexts] [draw] ...
        context_index = 3 if exe_name == "yolov5_thread_pool" else 4
        if len(command) > context_index:
            command[context_index] = str(request.contexts)
    stdbuf = shutil.which("stdbuf")
    if stdbuf:
        command = [stdbuf, "-oL", "-eL"] + command
    return command


def public_state() -> Dict[str, Any]:
    return {
        "running": state.running,
        "recording": state.recording,
        "started_at": state.started_at,
        "uptime_sec": round(time.time() - state.started_at, 3) if state.started_at else 0,
        "source": state.source,
        "contexts": state.contexts,
        "frames": state.frames,
        "fps": round(state.fps, 2),
        "last_error": state.last_error,
        "last_result": state.last_result,
        "streaming": state.stream_process is not None and state.stream_process.returncode is None,
    }


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


async def read_pipeline_output(process: asyncio.subprocess.Process) -> None:
    assert process.stdout is not None
    log_dir = Path("/var/log/rk3588-camera")
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "pipeline.log"
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
        if event["type"] == "detection":
            state.frames += 1
            state.last_result = event["payload"]
            if "fps" in event["payload"]:
                try:
                    state.fps = float(event["payload"]["fps"])
                except (TypeError, ValueError):
                    pass
        elif event["payload"]["level"] == "error":
            state.last_error = event["payload"]["message"]
        await broadcast(event)

    return_code = await process.wait()
    state.running = False
    state.pipeline_process = None
    await broadcast({"type": "status", "payload": public_state()})
    await broadcast({"type": "log", "payload": {"level": "info", "message": f"pipeline exited with code {return_code}"}})


async def simulate_detections() -> None:
    labels = ["person", "car", "bicycle", "dog"]
    last_time = time.time()
    while state.running:
        await asyncio.sleep(0.25)
        now = time.time()
        state.frames += 1
        state.fps = 1.0 / max(now - last_time, 1e-6)
        last_time = now
        detections = [
            {
                "label": random.choice(labels),
                "score": round(random.uniform(0.55, 0.95), 3),
                "box": {
                    "x": random.randint(20, 520),
                    "y": random.randint(20, 320),
                    "w": random.randint(60, 180),
                    "h": random.randint(60, 180),
                },
            }
            for _ in range(random.randint(1, 4))
        ]
        result = {
            "frame_id": state.frames,
            "timestamp": now,
            "fps": round(state.fps, 2),
            "detections": detections,
        }
        state.last_result = result
        await broadcast({"type": "detection", "payload": result})
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


@app.post("/api/pipeline/start", response_model=CommandResponse)
@app.put("/api/pipeline", response_model=CommandResponse)
async def start_pipeline(request: StartRequest) -> CommandResponse:
    if state.running:
        raise HTTPException(status_code=409, detail="pipeline is already running")

    state.running = True
    state.started_at = time.time()
    state.source = request.source
    state.contexts = request.contexts
    state.frames = 0
    state.fps = 0.0
    state.last_error = None
    state.last_result = None

    if request.dry_run:
        state.simulator_task = asyncio.create_task(simulate_detections())
        await broadcast({"type": "status", "payload": public_state()})
        return CommandResponse(ok=True, message="simulated pipeline started")

    command = build_pipeline_command(request)
    try:
        state.pipeline_process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(ROOT_DIR),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        state.pipeline_task = asyncio.create_task(read_pipeline_output(state.pipeline_process))
    except Exception as exc:
        state.running = False
        state.last_error = str(exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message="pipeline started")


@app.post("/api/pipeline/stop", response_model=CommandResponse)
@app.delete("/api/pipeline", response_model=CommandResponse)
async def stop_pipeline() -> CommandResponse:
    if state.simulator_task:
        state.running = False
        state.simulator_task.cancel()
        state.simulator_task = None
    await stop_process(state.pipeline_process)
    state.running = False
    state.pipeline_process = None
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message="pipeline stopped")


@app.post("/api/recording/start", response_model=CommandResponse)
@app.put("/api/recording", response_model=CommandResponse)
async def start_recording() -> CommandResponse:
    if state.recording:
        raise HTTPException(status_code=409, detail="recording is already running")

    config = load_config()
    command = list(config.get("record_command") or [])
    if command:
        try:
            state.record_process = await asyncio.create_subprocess_exec(*command, cwd=str(ROOT_DIR))
        except Exception as exc:
            state.last_error = str(exc)
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    state.recording = True
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message="recording started")


@app.post("/api/recording/stop", response_model=CommandResponse)
@app.delete("/api/recording", response_model=CommandResponse)
async def stop_recording() -> CommandResponse:
    await stop_process(state.record_process)
    state.record_process = None
    state.recording = False
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message="recording stopped")


@app.post("/api/stream/start", response_model=CommandResponse)
@app.put("/api/stream", response_model=CommandResponse)
async def start_stream() -> CommandResponse:
    if state.stream_process is not None and state.stream_process.returncode is None:
        raise HTTPException(status_code=409, detail="stream is already running")
    config = load_config()
    command = list(config.get("stream_command") or [])
    if not command:
        raise HTTPException(status_code=400, detail="stream_command is empty")
    try:
        state.stream_process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(ROOT_DIR),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except Exception as exc:
        state.last_error = str(exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message="stream started")


@app.post("/api/stream/stop", response_model=CommandResponse)
@app.delete("/api/stream", response_model=CommandResponse)
async def stop_stream() -> CommandResponse:
    await stop_process(state.stream_process)
    state.stream_process = None
    await broadcast({"type": "status", "payload": public_state()})
    return CommandResponse(ok=True, message="stream stopped")


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
