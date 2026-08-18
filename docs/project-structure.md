# 项目结构

本仓库是独立的 RK3588 智能跌倒检测项目，只保留 Python YOLOv8-Pose 推理、Web 控制台、部署配置和对应工具。

```text
.
├── apps/
│   ├── fall_detection/        # 姿态推理、跟踪、动作规则和事件录像
│   └── web/
│       ├── backend/           # FastAPI、摄像头进程、告警与资源监控
│       └── frontend/          # Vue 控制台
├── assets/
│   ├── calibration/           # 本地校准集，Git 忽略
│   ├── media/                 # 本地测试视频，大文件 Git 忽略
│   └── weights/               # RKNN 模型，Git 忽略
├── deploy/                    # Web 服务安装、systemd、转推和验收
├── docs/                      # 使用、部署、验证和工程记录
├── media/README/              # README 演示资源
├── tests/                     # Python 单元测试
└── tools/                     # 模型转换、校准审计和摄像头诊断
```

## 模块边界

- `apps/fall_detection` 不依赖 Web，可独立读取 RTSP 或视频并输出逐帧 JSON。
- `apps/web/backend` 负责进程编排、配置持久化、告警数据库、录像接口和系统指标。
- `apps/web/frontend` 只通过 HTTP/WebSocket 与后端交互。
- `deploy` 只放板端运行所需脚本，不保存模型、账号密码或运行数据。
- `tools` 只保留当前姿态模型和摄像头链路仍会使用的辅助程序。

运行产生的 `.runtime/`、校准图片、RKNN 模型、录像和日志均不纳入源码版本控制。
