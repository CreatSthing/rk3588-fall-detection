# RK3588 Fall Detection

面向 Rockchip RK3588 的实时跌倒检测工程。系统使用 YOLOv8n-Pose RKNN 获取人体关键点，通过轻量跟踪和时序状态机判断跌倒，并将告警实时推送到浏览器，同时保存跌倒前后的事件录像。仓库保留原 YOLOv5 流媒体流水线，作为 RK3588 解码、推理、编码和推流基础。

## 核心功能

- YOLOv8n-Pose RKNN 姿态推理
- 多人轻量跟踪与时序跌倒判断
- WebSocket 实时告警、声音提示和告警确认
- SQLite 告警记录与录像索引
- 默认保存跌倒前 5 秒、后 10 秒事件录像
- FFmpeg RTSP 兜底录像

## 原有媒体能力

- 单张图片 YOLOv5 推理
- 本地视频目标检测
- 多线程推理线程池
- 视频流解码、推理和编码
- 基于 ZLMediaKit 的流媒体处理
- 支持 RKNN 量化与非量化模型
- 可选的 PC 端 YOLOv5 ONNX 流程测试

部署、接口和现场标定见 [`docs/fall-detection.md`](docs/fall-detection.md)。
2026-08-17 的 RK3588 实机结果、故障原因和回滚步骤见 [`docs/rk3588-fall-deployment-report-20260817.md`](docs/rk3588-fall-deployment-report-20260817.md)。

## 目录结构

```text
.
├── apps/
│   ├── image_demo/       # 单图检测入口
│   ├── video_demo/       # 本地视频检测入口
│   ├── thread_pool_demo/ # 多线程检测入口
│   ├── stream_demo/      # 原始流处理入口
│   ├── stream_pipeline/  # Pipeline 化实时流处理入口
│   ├── pc_yolov5/        # PC 端 OpenCV DNN 测试入口
│   ├── fall_detection/   # 姿态推理、跟踪、跌倒判断与事件录像
│   └── web/              # 告警后端与浏览器前端
├── src/
│   ├── engine/          # 推理引擎抽象和 RKNN 实现
│   ├── process/         # YOLOv5 前处理和后处理
│   ├── yolov5/          # YOLOv5 模型封装和线程池
│   ├── media/           # FFmpeg、MPP、ZLMediaKit 媒体处理
│   ├── pipeline/        # 实时视频分析流程编排
│   ├── draw/            # 检测框绘制
│   ├── pc/              # PC 端 OpenCV DNN 后端实现
│   ├── types/           # 通用数据结构和错误码
│   └── utils/           # 日志和辅助函数
├── assets/
│   ├── labels/          # 类别标签
│   ├── media/           # 测试素材
│   └── weights/         # RKNN 模型
├── docs/                # 工程记录和结构说明
├── librknn_api/include/ # RKNN Runtime 头文件
├── 3rdparty/rga/        # RGA 头文件
├── mpp_libs/            # MPP/ZLMediaKit 库放置目录
└── CMakeLists.txt       # CMake 构建配置
```

更完整的结构优化建议见 [`docs/project-structure.md`](docs/project-structure.md)。

## 环境要求

- Rockchip RK3588，64 位 ARM Linux
- CMake 3.11 或更高版本
- 支持 C++14 的编译器
- RKNN Runtime
- Rockchip MPP 与 RGA
- OpenCV
- FFmpeg（`avformat`、`avcodec`、`avutil`）
- ZLMediaKit C API

## 第三方依赖

为控制仓库体积，OpenCV SDK、平台预编译动态库以及大型视频样例未纳入 Git。构建前请根据目标系统准备依赖，并将 RKNN、MPP、RGA 和 ZLMediaKit 库放到 `CMakeLists.txt` 所使用的位置，或修改其中的搜索路径。

## 构建

```bash
mkdir -p build
cd build
cmake ..
cmake --build . -j$(nproc)
```

可执行目标包括：

- `yolov5_img`
- `yolov5_video`
- `yolov5_thread_pool`
- `yolov5_stream`
- `yolov5_stream_pool`
- `rknn_benchmark`

## RKNN 模型测速

`rknn_benchmark` 只测 RKNN 输入、NPU 推理和输出获取，不包含图片解码、YOLO 后处理或画框。默认先预热 10 次，再连续测量 100 次，并输出平均延迟、P50、P95 和等效 FPS：

```bash
rknn_benchmark model.rknn
```

也可指定测量次数和预热次数：

```bash
rknn_benchmark model.rknn 200 20
```

## PC 端流程测试

`pc_yolov5` 用于在没有 RK3588 开发板时验证“读取输入、YOLO 推理、后处理、画框、保存结果”的基本流程。它使用 OpenCV DNN 加载常见的 YOLOv5 ONNX 模型，不依赖 RKNN、MPP、RGA 或 ZLMediaKit。

Windows PowerShell 构建：

```powershell
cmake -S . -B build-pc -DBUILD_RK3588_TARGETS=OFF -DBUILD_PC_YOLO_DEMO=ON -DOpenCV_DIR=C:/path/to/opencv/cmake
cmake --build build-pc --config Release
```

Linux/macOS Shell 构建：

```bash
cmake -S . -B build-pc \
  -DBUILD_RK3588_TARGETS=OFF \
  -DBUILD_PC_YOLO_DEMO=ON \
  -DOpenCV_DIR=/path/to/opencv/cmake
cmake --build build-pc --config Release
```

测试图片：

```bash
pc_yolov5 yolov5s.onnx input.jpg result.jpg assets/labels/coco_80_labels_list.txt
```

测试视频：

```bash
pc_yolov5 yolov5s.onnx input.mp4 result.mp4 assets/labels/coco_80_labels_list.txt
```

测试 PC 摄像头，输入 `0` 表示第一个摄像头，按 `Esc` 停止：

```bash
pc_yolov5 yolov5s.onnx 0 camera_result.mp4 assets/labels/coco_80_labels_list.txt
```

当前 PC 后端只支持输出形状为 `[1, N, 5 + 类别数]` 的常见 YOLOv5 ONNX 模型。ONNX 模型不提交到仓库，可由原始 YOLOv5 工程导出后放入本地测试目录。

## 模型与测试素材

`assets/weights/` 中包含 RKNN 模型。`assets/media/` 中保留了小型图片样例；大型视频文件因 GitHub 文件大小限制未提交，可自行准备视频或流地址进行测试。

## 注意事项

本工程依赖 RK3588 平台相关运行库，建议直接在开发板上构建，或使用正确配置的 aarch64 交叉编译环境。
