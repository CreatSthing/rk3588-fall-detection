# RK3588 智能跌倒检测

基于 YOLOv8n-Pose 和 RK3588 NPU 的实时跌倒检测系统。系统从 RTSP 摄像头提取人体关键点，通过人员跟踪和时序规则判断跌倒，并提供 Web 预览、告警、录像及设备监控。

```text
RTSP 摄像头 → FFmpeg 解码 → YOLOv8n-Pose RKNN
             → 人员跟踪/跌倒判断 → WebSocket、SQLite、MP4
```

## 功能

- YOLOv8n-Pose 17点姿态推理，支持多人跟踪
- 识别站立、行走、坐姿、躺卧、起身、落座和跌倒
- 浏览器管理多路 RTSP 摄像头，修改配置后自动重连
- 同帧显示视频、人员框和骨架
- 可调告警置信度，支持声音通知、确认和删除
- 自动保存跌倒前后录像，支持手动录像、播放和下载
- 显示 CPU、内存、温度、NPU 三核和 RGA 负载

> 动作状态由关键点几何和短时运动规则判断，不是独立的动作分类模型。正式使用前需要按机位标定阈值。

## 实机结果

在 Orange Pi 5 Ultra、640×360 @ 20 FPS 摄像头上，当前 INT8 模型实时处理约 19 FPS。272帧跌倒视频中检出271帧并触发一次告警；INT8 NPU 耗时约32.7 ms，FP16约72.1 ms。完整结果见 [`deploy/fall-model-manifest.json`](deploy/fall-model-manifest.json)。

[![跌倒报警演示](media/README/fall-alarm-demo.gif)](media/README/fall-alarm-demo.mp4?raw=1)

点击动图可打开或下载原始 MP4。

![Web 控制台](media/README/image-20260818002558850.png)

## 项目结构

```text
apps/fall_detection/   姿态推理、跟踪、跌倒规则和事件录像
apps/web/              FastAPI 后端与 Vue 前端
assets/                模型、校准集和本地测试素材
deploy/                安装、systemd、转推和验收脚本
docs/                  配置、标定和验证文档
tests/                 Python 单元测试
tools/                 模型转换、校准和诊断工具
```

模型、校准图片和大体积测试视频默认不提交 Git。

## 复现

### 1. 准备开发板

要求：RK3588/RK3588S 64位 Linux、Python 3.8+、FFmpeg、OpenCV、RKNN Runtime 2.3.2，以及匹配 Python 版本的 `rknn-toolkit-lite2` 2.3.2 wheel。

```bash
sudo apt update
sudo apt install -y git ffmpeg python3-venv python3-opencv python3-numpy

sudo mkdir -p /opt/rk3588-camera
sudo git clone https://github.com/CreatSthing/rk3588-fall-detection.git \
  /opt/rk3588-camera/current
sudo chown -R "$(id -un):$(id -gn)" /opt/rk3588-camera/current
cd /opt/rk3588-camera/current
```

RKNN Runtime 和 RKNNLite 可从 [RKNN-Toolkit2 v2.3.2](https://github.com/airockchip/rknn-toolkit2/releases/tag/v2.3.2) 获取。

### 2. 准备模型

将以下文件放入 `assets/weights/`：

```text
yolov8n-pose-int8-calibrated-20260818.rknn
```

SHA-256：

```text
df98f844b19c7b75b8ffc376294678ed6bf0e510a50582da0574ec8ee1cde622
```

将匹配板端 Python 的 RKNNLite wheel 放入 `vendor/`。如需从 ONNX 重新量化：

```bash
python3 tools/convert_yolov8_pose_onnx_to_rknn.py \
  yolov8n-pose.onnx \
  assets/calibration/pose-int8-20260818/dataset.txt \
  assets/weights/yolov8n-pose-int8-calibrated-20260818.rknn
```

当前模型使用126张本地姿态、COCO人体及负样本混合校准图；生成方法见 `tools/prepare_pose_calibration_dataset.py`。

### 3. 安装服务

```bash
sudo ./deploy/install_web_service.sh /opt/rk3588-camera/current
```

编辑 `/var/lib/rk3588-camera/web.json` 中的 RTSP 地址，或打开 Web 控制台后在摄像头管理中修改并选择“保存并重新连接”。配置模板位于 [`apps/web/backend/config.example.json`](apps/web/backend/config.example.json)。

```bash
sudo systemctl restart rk3588-web
systemctl status rk3588-web --no-pager
journalctl -u rk3588-web -f
```

浏览器访问：

```text
http://开发板IP:8000
```

### 4. 验证

验证摄像头和 NPU 推理：

```bash
./deploy/verify_fall_deployment.sh \
  /opt/rk3588-camera/current \
  'rtsp://user:password@camera-ip:554/stream2' 30
```

运行不依赖 NPU 的测试：

```bash
python -m unittest discover -s tests -v
```

## Docker 部署

Docker 只在 RK3588 板端构建和运行；宿主机仍需提供 NPU 驱动、RKNN Runtime 2.3.2 和 MediaMTX。

将 RKNNLite、NumPy ARM64 wheel 放入 `vendor/`，模型放入 `assets/weights/`。先构建成功再切换服务：

```bash
docker compose build
sudo systemctl disable --now rk3588-web
docker compose up -d
docker compose ps
docker compose logs -f
```

首次启动会创建 `/var/lib/rk3588-camera/web.json`。已有配置和录像目录会直接复用；如需恢复 systemd 部署：

```bash
docker compose down
sudo systemctl enable --now rk3588-web
```

容器使用 host network 访问宿主 MediaMTX，并挂载 `/dev/dri`、`/usr/lib/librknnrt.so` 和 debugfs。当前为单机可信环境使用 `privileged`；不要把该配置直接用于多租户服务器。

## 数据位置

| 内容 | 板端位置 |
| --- | --- |
| 运行配置 | `/var/lib/rk3588-camera/web.json` |
| 告警数据库与事件录像 | `/var/lib/rk3588-camera/events` |
| 手动录像 | `/var/lib/rk3588-camera/recordings` |
| 服务日志 | `journalctl -u rk3588-web -f` |

## 文档

- [跌倒规则、告警和现场标定](docs/fall-detection.md)
- [Web 控制台配置与 API](docs/web-console.md)
- [RK3588 部署与故障排查](docs/rk3588-fall-deployment-report-20260817.md)

## 注意

- 不要把包含账号密码的 RTSP 地址提交到 Git。
- 上线前应测试跌倒、快速坐下、弯腰、下蹲、躺卧、遮挡、夜间和多人场景。
- 本项目是视觉辅助告警系统，不能替代人工看护或生命安全系统。
