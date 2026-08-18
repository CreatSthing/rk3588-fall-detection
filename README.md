# RK3588 智能跌倒检测

基于 YOLOv8n-Pose 和 RK3588 NPU 的实时跌倒检测系统，支持 RTSP 摄像头、多人跟踪、Web 预览、跌倒告警和录像。

```text
RTSP 摄像头 → FFmpeg → YOLOv8n-Pose RKNN
             → 跟踪与跌倒规则 → WebSocket / SQLite / MP4
```

## 功能

- YOLOv8n-Pose 17 点姿态推理与多人跟踪
- 识别站立、行走、坐姿、躺卧、起身、落座和跌倒
- 浏览器管理摄像头，保存配置后自动重连
- 可调告警置信度，支持声音通知、确认和删除
- 自动保存告警录像，支持手动录像、播放和下载
- 展示 CPU、内存、温度、NPU 三核和 RGA 负载

> 动作状态来自关键点几何和短时运动规则，不是独立动作分类模型；实际部署前需要按机位标定。

## 实机结果

Orange Pi 5 Ultra、640×360 @ 20 FPS 下，INT8 模型实时处理约 19 FPS。272 帧跌倒视频检出 271 帧并触发一次告警；INT8 NPU 约 32.7 ms，FP16 约 72.1 ms。详见 [`deploy/fall-model-manifest.json`](deploy/fall-model-manifest.json)。

[![跌倒报警演示](media/README/fall-alarm-demo.gif)](media/README/fall-alarm-demo.mp4?raw=1)

![Web 控制台](media/README/image-20260818002558850.png)

## 运行要求

- RK3588/RK3588S 64 位 Linux
- NPU 驱动、RKNN Runtime 2.3.2 和 MediaMTX
- systemd 模式：Python 3.8+、FFmpeg、OpenCV
- Docker 模式：Docker Engine 与 Docker Compose
- 与 Python、Runtime 匹配的 `rknn-toolkit-lite2` 2.3.2 ARM64 wheel

RKNN Runtime 和 RKNNLite 可从 [RKNN-Toolkit2 v2.3.2](https://github.com/airockchip/rknn-toolkit2/releases/tag/v2.3.2) 获取。

## 准备项目

```bash
sudo mkdir -p /opt/rk3588-camera
sudo git clone https://github.com/CreatSthing/rk3588-fall-detection.git \
  /opt/rk3588-camera/current
sudo chown -R "$(id -un):$(id -gn)" /opt/rk3588-camera/current
cd /opt/rk3588-camera/current
```

将模型放入 `assets/weights/`：

```text
yolov8n-pose-int8-calibrated-20260818.rknn
SHA-256: df98f844b19c7b75b8ffc376294678ed6bf0e510a50582da0574ec8ee1cde622
```

将 RKNNLite wheel 放入 `vendor/`。Docker 模式还需放入 Python 3.8 对应的 NumPy ARM64 wheel，具体文件名见 [`vendor/README.md`](vendor/README.md)。模型和 wheel 默认不提交 Git。

## 部署方式

项目同时支持 systemd 和 Docker，两者功能及数据目录相同，但不能同时运行：它们都会监听 `8000` 端口。

| 模式 | 适合场景 | 管理命令 |
| --- | --- | --- |
| systemd | 直接使用板端环境，方便底层调试 | `systemctl`、`journalctl` |
| Docker | 环境隔离，部署和升级更方便 | `docker compose` |

### 方式一：systemd

```bash
sudo apt update
sudo apt install -y git ffmpeg python3-venv python3-opencv python3-numpy
sudo ./deploy/install_web_service.sh /opt/rk3588-camera/current

systemctl status rk3588-web --no-pager
journalctl -u rk3588-web -f
```

### 方式二：Docker

Docker 在 RK3588 板端构建和运行；宿主机继续提供 NPU 驱动、`librknnrt.so` 和 MediaMTX。

```bash
docker compose build
sudo systemctl disable --now rk3588-web
docker compose up -d

docker compose ps
docker compose logs -f
```

容器使用 host network，并挂载 `/dev/dri`、`/usr/lib/librknnrt.so`、debugfs 和持久化数据目录。当前 Compose 配置适用于单机可信环境，不建议直接用于多租户服务器。

### 两种模式切换

systemd 切换到 Docker：

```bash
docker compose build
sudo systemctl disable --now rk3588-web
docker compose up -d
```

Docker 切换到 systemd：

```bash
docker compose down
sudo ./deploy/install_web_service.sh /opt/rk3588-camera/current
```

启动脚本会自动转换共享配置中的程序路径，摄像头配置、告警和录像不会丢失。

## 使用与验证

浏览器访问：

```text
http://开发板IP:8000
```

可在“摄像头管理”修改 RTSP 地址并选择“保存并重新连接”。运行配置位于 `/var/lib/rk3588-camera/web.json`。

systemd 模式可执行完整摄像头与 NPU 验证：

```bash
./deploy/verify_fall_deployment.sh \
  /opt/rk3588-camera/current \
  'rtsp://user:password@camera-ip:554/stream2' 30
```

通用单元测试：

```bash
python3 -m unittest discover -s tests -v
```

## 数据位置

| 内容 | 板端位置 |
| --- | --- |
| 配置 | `/var/lib/rk3588-camera/web.json` |
| 告警数据库与事件录像 | `/var/lib/rk3588-camera/events` |
| 手动录像 | `/var/lib/rk3588-camera/recordings` |
| systemd 日志 | `journalctl -u rk3588-web -f` |
| Docker 日志 | `docker compose logs -f` |

## 项目结构

```text
apps/fall_detection/   推理、跟踪、跌倒规则和事件录像
apps/web/              FastAPI 后端与 Web 前端
assets/                模型、校准集和测试素材
deploy/                安装、服务和验收脚本
docs/                  配置、标定和验证文档
tests/                 单元测试
tools/                 模型转换、校准和诊断工具
```

更多说明：[`跌倒检测与标定`](docs/fall-detection.md) · [`Web 控制台`](docs/web-console.md) · [`部署与排障`](docs/rk3588-fall-deployment-report-20260817.md)

## 注意

- 不要将包含账号密码的 RTSP 地址提交到 Git。
- 上线前应测试跌倒、快速坐下、弯腰、下蹲、躺卧、遮挡、夜间和多人场景。
- 本项目是视觉辅助告警系统，不能替代人工看护或生命安全系统。
