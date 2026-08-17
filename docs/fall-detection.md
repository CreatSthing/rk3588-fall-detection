# RK3588 跌倒检测、告警与事件录像

2026-08-17 实机部署、问题分析、校验值和回滚记录见 [`rk3588-fall-deployment-report-20260817.md`](rk3588-fall-deployment-report-20260817.md)。

## 整体链路

```text
摄像头 / RTSP
  -> YOLOv8n-Pose INT8（RK3588 NPU）
  -> IoU + 中心距离人员跟踪
  -> 7类动作（站立/行走/坐姿/躺卧/起身/落座/跌倒）
  -> 关键点下落速度 + 躯干角度 + 人体宽高比
  -> 0.7 秒时序确认
  -> JSON 告警
  -> FastAPI + SQLite 持久化
  -> WebSocket 推送前端
  -> 红色告警、声音/系统通知、确认与录像查看
```

`apps/fall_detection/main.py` 在内存中保留默认 5 秒 JPEG 帧环形缓存。跌倒确认后再继续保留 10 秒，异步写成一段 MP4，因此录像中包含跌倒前后过程。如果其他检测程序只上报告警而不产生录像，Web 后端会自动用 FFmpeg 从 RTSP 录制默认 20 秒作为兜底。

检测 JSON 同时输出 17 个 COCO 关键点、`action`、中文 `action_label`和 `fall_state`。前端根据关键点绘制骨架，候选/确认跌倒时改为红色。

为避免 WebRTC 播放延迟导致骨架与人体错位，管线默认每2帧携带一张与关键点完全同步的 JPEG，前端只用这一对数据同时更新“AI检测画面”和 SVG。可用 `--preview-every 0` 禁用预览图。

网络流由后台线程持续解码、只保留最新完整帧。输入帧率高于 NPU 推理帧率时会主动跳过旧帧，并在结果中累计 `source_frames_dropped`，因此不会因为 FIFO 排队逐渐落后几分钟。本地离线视频不启用跳帧。

## 模型准备

使用 Rockchip RKNN Model Zoo 提供的优化版 `yolov8n-pose.onnx`，不要直接用 Ultralytics 原始导出的单输出 ONNX 替换，两者的输出格式不同。

当前生产配置使用 FP16。初版 INT8 的40张量化图只有空场景、没有代表性人体姿态，使目标置信度正样本被截到原始 logit `0.0`，sigmoid 后全部为 `0.500`，会把手或物体边缘误判为人。没有合格人体量化集时不要使用该 INT8 文件。

在 x86 Ubuntu 的 RKNN-Toolkit2 2.3.2 环境转换 FP16：

```bash
python3 tools/convert_yolov8_pose_onnx_to_rknn.py \
  yolov8n-pose.onnx pose_dataset.txt \
  assets/weights/yolov8n-pose-fp16.rknn --fp16
```

以后如需重新做 INT8，量化集必须覆盖站立、走动、坐下、躺卧、起身、落座、跌倒、弯腰、遮挡和空场景，并与独立验证集比较后才能替换。转换使用的 RKNN-Toolkit2、板端 `librknnrt.so` 和 `rknn-toolkit-lite2` 必须同版本；本次统一使用2.3.2，校验值见 `deploy/fall-model-manifest.json`。

## 启动姿态检测程序

```bash
cd /opt/rk3588-camera/current
.venv/bin/python -m apps.fall_detection.main \
  --model assets/weights/yolov8n-pose-fp16.rknn \
  --source rtsp://127.0.0.1:8554/live/cam1 \
  --camera-id cam1 \
  --event-dir /var/lib/rk3588-camera/events \
  --decoder ffmpeg-software
```

当前 Orange Pi 镜像的 RKMPP/RGA 对640×360流存在步长转换错误，因此网络流显式使用 FFmpeg 软件解码；FP16 和同步预览开启后的实测稳定值约11.4 FPS。

`config.example.json` 已将新增摄像头的默认 pipeline 指向该程序。`pipeline_command` 支持 `{source}`、`{camera_id}` 和 `{contexts}` 占位符。

## 告警 API

```text
GET  /api/events?limit=100&camera_id=cam1
POST /api/cameras/{camera_id}/fall-events
POST /api/events/{event_id}/acknowledge
GET  /api/events/{event_id}/video
WS   /ws/detections
```

其他 C++/算法进程只要在每帧 JSON 的 `events` 中输出以下结构，也可复用同一套告警和录像后端：

```json
{
  "events": [{
    "id": "unique-event-id",
    "event_type": "fall",
    "state": "confirmed",
    "track_id": 3,
    "confidence": 0.91,
    "timestamp": 1786900000.0,
    "details": {"torso_angle": 68.0, "descent_speed": 0.72}
  }]
}
```

## 现场标定

默认下降阈值 `0.22` 是根据一段公开真实跌倒视频在 RK3588 上回放后得到的起点，不是最终生产阈值。至少录制以下样本做回放验证：真实/模拟跌倒、快速坐下、弯腰捡物、下蹲、躺床、遮挡、夜间和多人交叉。根据误报和漏报调整 `--descent-threshold`、`--confirm-seconds` 和关键点置信度。

本功能是视觉辅助告警，不能替代医疗看护或生命安全系统。
