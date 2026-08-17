from __future__ import annotations

import asyncio
import json
import os
import random
import re
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


class CameraUpsertRequest(BaseModel):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,32}$")
    name: str = ""
    source_url: str = Field(min_length=1)
    width: int = Field(default=640, ge=1, le=7680)
    height: int = Field(default=360, ge=1, le=4320)
    contexts: int = Field(default=8, ge=1, le=20)
    decoder: str = "software"


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
    last_domain_samples: Dict[str, Dict[str, int]] = field(default_factory=dict)


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


def make_camera_config(request: CameraUpsertRequest) -> Dict[str, Any]:
    camera_id = request.id
    mediamtx_path = f"/live/{camera_id}"
    return {
        "id": camera_id,
        "name": request.name or camera_id,
        "width": request.width,
        "height": request.height,
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
            "/opt/rk3588-camera/current/bin/yolov5_thread_pool",
            "/opt/rk3588-camera/current/assets/weights/yolov5s_raw_heads_int8.rknn",
            f"rtsp://127.0.0.1:8554{mediamtx_path}",
            str(request.contexts),
            "0",
            request.decoder,
            f"/tmp/web_pipeline_{camera_id}.frames.csv",
            "1",
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


def read_text(path: str) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return None


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
    debug_load = parse_load_percent(read_text("/sys/kernel/debug/rknpu/load"))
    debug_freq = read_int("/sys/kernel/debug/rknpu/freq")
    debug_power = read_text("/sys/kernel/debug/rknpu/power")
    if debug_load is not None:
        metric["load_percent"] = debug_load
        metric["available"] = True
    if debug_freq is not None:
        metric["freq_mhz"] = round(debug_freq / 1_000_000, 1) if debug_freq > 100000 else debug_freq
    if debug_power is not None:
        metric["power"] = debug_power
    return metric


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
    return {
        "timestamp": time.time(),
        "cpu": {"usage_percent": cpu_usage_percent()},
        "memory": memory_metrics(),
        "temperatures": temperature_metrics(),
        "npu": debug_npu_metric(),
        "mpp": {
            "decoder": [domain_busy_percent("rkvdec0"), domain_busy_percent("rkvdec1")],
            "encoder": [domain_busy_percent("venc0"), domain_busy_percent("venc1")],
        },
        "rga": {
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
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.clients.discard(websocket)


app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")
