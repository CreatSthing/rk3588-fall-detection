# RK3588 跌倒检测、告警与事件录像

2026-08-17 实机部署、问题分析、校验值和回滚记录见 [`rk3588-fall-deployment-report-20260817.md`](rk3588-fall-deployment-report-20260817.md)。

## 整体链路

```text
摄像头 / RTSP
  -> YOLOv8n-Pose INT8（RK3588 NPU）
  -> IoU 人员跟踪
  -> 关键点下落速度 + 躯干角度 + 人体宽高比
  -> 0.7 秒时序确认
  -> JSON 告警
  -> FastAPI + SQLite 持久化
  -> WebSocket 推送前端
  -> 红色告警、声音/系统通知、确认与录像查看
```

`apps/fall_detection/main.py` 在内存中保留默认 5 秒 JPEG 帧环形缓存。跌倒确认后再继续保留 10 秒，异步写成一段 MP4，因此录像中包含跌倒前后过程。如果其他检测程序只上报告警而不产生录像，Web 后端会自动用 FFmpeg 从 RTSP 录制默认 20 秒作为兜底。

## 模型准备

使用 Rockchip RKNN Model Zoo 提供的优化版 `yolov8n-pose.onnx`，不要直接用 Ultralytics 原始导出的单输出 ONNX 替换，两者的输出格式不同。

1. 在 x86 Ubuntu 的 RKNN-Toolkit2 环境下准备 50～200 张实际安装场景图片，每行一个路径写入 `pose_dataset.txt`。
2. 转换：

```bash
python3 tools/convert_yolov8_pose_onnx_to_rknn.py \
  yolov8n-pose.onnx pose_dataset.txt \
  assets/weights/yolov8n-pose-int8.rknn
```

3. 确保转换使用的 RKNN-Toolkit2、板端 `librknnrt.so` 和 `rknn-toolkit-lite2` 来自同一版本。本次实机统一使用2.3.2；完整校验值见 `deploy/fall-model-manifest.json`。

## 启动姿态检测程序

```bash
cd /opt/rk3588-camera/current
.venv/bin/python -m apps.fall_detection.main \
  --model assets/weights/yolov8n-pose-int8.rknn \
  --source rtsp://127.0.0.1:8554/live/cam1 \
  --camera-id cam1 \
  --event-dir /var/lib/rk3588-camera/events \
  --decoder ffmpeg-software
```

当前 Orange Pi 镜像的 RKMPP/RGA 对640×360流存在步长转换错误，因此网络流显式使用 FFmpeg 软件解码；实测仍可达到约17～20 FPS。

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

默认参数是安全起点，不是最终生产阈值。至少录制以下样本做回放验证：真实/模拟跌倒、快速坐下、弯腰捡物、下蹲、躺床、遮挡、夜间和多人交叉。根据误报和漏报调整 `--descent-threshold`、`--confirm-seconds` 和关键点置信度。

本功能是视觉辅助告警，不能替代医疗看护或生命安全系统。
