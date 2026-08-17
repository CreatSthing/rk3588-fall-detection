# RK3588 Web 控制台

这个目录提供一个轻量的 FastAPI + Vue 控制台，用来把“多路摄像头推流、浏览器控制检测进程、查看检测结果”这条链路跑通。

## 目标

- REST API 按摄像头控制检测流程：启动、停止、查询状态、开始录像、停止录像。
- WebSocket 实时推送检测结果到浏览器。
- 前端按 `cameras[]` 多路展示视频和检测框。
- 前端用 Vue 3 做单页控制台，当前不依赖 Node/Vite 构建链，方便直接部署到 RK3588 板子。

## 目录

```text
apps/web/
├── backend/
│   ├── app.py
│   ├── config.example.json
│   └── requirements.txt
└── frontend/
    ├── index.html
    ├── app.js
    └── style.css
```

## 启动

```bash
cd /opt/rk3588-camera/current
python3 -m venv .venv
. .venv/bin/activate
pip install -r apps/web/backend/requirements.txt
uvicorn apps.web.backend.app:app --host 0.0.0.0 --port 8000
```

浏览器访问：

```text
http://板子IP:8000
```

## API

```text
GET  /api/status
GET  /api/cameras
POST /api/cameras/{camera_id}/stream/start
POST /api/cameras/{camera_id}/stream/stop
POST /api/cameras/{camera_id}/pipeline/start
POST /api/cameras/{camera_id}/pipeline/stop
POST /api/cameras/{camera_id}/recording/start
POST /api/cameras/{camera_id}/recording/stop

# 兼容旧版单路接口，默认映射到 cam1
PUT  /api/pipeline
DELETE /api/pipeline
PUT  /api/recording
DELETE /api/recording
POST /api/pipeline/start
POST /api/pipeline/stop
POST /api/recording/start
POST /api/recording/stop
WS   /ws/detections
```

启动检测请求示例：

```json
{
  "source": "/opt/rk3588-camera/current/assets/media/c3_1080.annexb.h264",
  "contexts": 8,
  "dry_run": false
}
```

`dry_run=true` 时不会启动真实检测进程，而是后端模拟检测框，适合先验证网页和实时推送是否正常。

## 后端如何接真实检测

后端通过 `config.example.json` 里的 `cameras[]` 配置多路摄像头。每一路都有自己的：

```text
id/name
width/height
player_url/rtsp_url/hls_url
stream_command
pipeline_command
record_command
```

前端检测框按每路的 `width/height` 做坐标归一化。比如 TP-Link 子码流是 `640x360`，检测框坐标也来自这一路 `640x360` 图像，就必须用 `640x360` 缩放；如果误用 `1920x1080`，框会明显变小，无法框住人。

当 C++ 检测程序后续输出 JSON 行时，后端会把它识别为检测结果并推送到浏览器：

```json
{
  "frame_id": 1,
  "timestamp": 1780000000.0,
  "fps": 80.5,
  "detections": [
    {
      "label": "person",
      "score": 0.92,
      "box": {"x": 120, "y": 80, "w": 160, "h": 300}
    }
  ]
}
```

`mpp_rga_thread_pool` 的最后一个参数 `json_events=1` 会开启这个输出。检测程序如果只输出普通日志，WebSocket 也会把日志推给浏览器，但不会形成可视化框。

## 后续可扩展

- 给 `mpp_rga_thread_pool` 增加 `--json-events`，每帧输出结构化检测结果。
- WebRTC 画面预览建议用 `deploy/run_gst_mpp_stream.sh`：
  - 本地 MP4 测试视频如果带 B 帧，脚本会走 `GStreamer + mpph264enc`，重新编码成无 B 帧 H.264，避免 MediaMTX WebRTC 报 `WebRTC doesn't support H264 streams with B-frames`。
  - 实时 RTSP 摄像头如果本身已经是 H.264 且无 B 帧，脚本默认直接 `-c:v copy` 转推，避免重复编码造成额外延迟。
  - 如果某路摄像头流浏览器不兼容，可以设置 `RTSP_REENCODE=1` 强制走 RK3588 MPP/VPU 硬件重编码。
- 录像接口接入 FFmpeg 或 MPP 编码输出。
- 增加系统监控接口：CPU、NPU、内存、温度、磁盘。
