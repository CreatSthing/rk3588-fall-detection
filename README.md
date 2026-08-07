# RK3588 YOLOv5 Stream

面向 Rockchip RK3588 的实时目标检测示例工程。项目使用 RKNN Runtime 运行 YOLOv5 模型，并结合 MPP、RGA、OpenCV、FFmpeg 与 ZLMediaKit 完成图像/视频解码、推理、绘制、编码和视频流处理。

## 功能

- 单张图片 YOLOv5 推理
- 本地视频目标检测
- 多线程推理线程池
- 视频流解码、推理和编码
- 基于 ZLMediaKit 的流媒体处理
- 支持 RKNN 量化与非量化模型

## 目录结构

```text
.
├── src/                 # 推理、媒体处理和绘制源码
├── weights/             # RKNN 模型
├── media/               # 小型图片测试素材
├── librknn_api/include/ # RKNN Runtime 头文件
├── 3rdparty/rga/        # RGA 头文件
├── mpp_libs/            # MPP/ZLMediaKit 库放置目录
└── CMakeLists.txt       # CMake 构建配置
```

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

## 模型与测试素材

`weights/` 中包含 RKNN 模型。`media/` 中保留了小型图片样例；大型视频文件因 GitHub 文件大小限制未提交，可自行准备视频或流地址进行测试。

## 注意事项

本工程依赖 RK3588 平台相关运行库，建议直接在开发板上构建，或使用正确配置的 aarch64 交叉编译环境。
