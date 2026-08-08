# 项目结构优化建议

本文档记录当前工程采用的克制版模块边界，以及后续继续重构时推荐的方向。

## 当前结构

```text
.
├── CMakeLists.txt          # 构建入口，定义 PC Demo 与 RK3588 目标
├── apps/                   # 可执行程序入口，只放 main 和参数解析
│   ├── image_demo/
│   ├── video_demo/
│   ├── thread_pool_demo/
│   ├── stream_demo/
│   ├── stream_pipeline/
│   └── pc_yolov5/
├── assets/                 # 模型、标签和测试素材
│   ├── labels/
│   ├── media/
│   └── weights/
├── docs/                   # 工程记录和结构说明
├── librknn_api/            # RKNN Runtime 头文件和本地库放置目录
├── 3rdparty/               # 第三方 SDK 头文件，主要是 RGA/OpenCV
├── mpp_libs/               # MPP、ZLMediaKit 等平台库放置目录
├── submodules/             # 外部源码依赖目录
└── src/
    ├── draw/               # 检测框绘制
    ├── engine/             # 推理引擎抽象和 RKNN 实现
    ├── media/              # FFmpeg、MPP、ZLMediaKit 媒体处理
    ├── pc/                 # PC 端 OpenCV DNN 流程测试
    ├── pipeline/           # 实时视频分析流程编排
    ├── process/            # YOLOv5 前处理和后处理
    ├── types/              # 通用数据结构和错误码
    ├── utils/              # 日志和引擎辅助函数
    └── yolov5/             # YOLOv5 模型封装与线程池
```

## 已识别的问题

- `CMakeLists.txt` 已经收敛了媒体源文件和平台库清单，但仍可以继续拆到 `cmake/` 辅助文件中。
- 第三方 SDK、平台预编译库和源码依赖散落在 `3rdparty/`、`librknn_api/`、`mpp_libs/`、`submodules/`，建议后续统一命名规则。
- `src/media/mpi_enc.cpp` 文件较大，后续可以按编码配置、帧封装、推流输出拆分。

## 后续推荐目标结构

```text
.
├── apps/
│   ├── rk3588_img/
│   ├── rk3588_video/
│   ├── rk3588_stream/
│   ├── rk3588_stream_pool/
│   └── pc_yolov5/
├── assets/
│   ├── labels/
│   ├── media/
│   └── weights/
├── cmake/
│   ├── Dependencies.cmake
│   └── Targets.cmake
├── docs/
├── external/
│   ├── rga/
│   ├── rknn/
│   ├── mpp/
│   └── zlmediakit/
└── src/
    ├── common/
    ├── inference/
    ├── media/
    ├── pipeline/
    └── vision/
```

## 后续建议迁移顺序

1. 把 RKNN、RGA、MPP、ZLMediaKit 相关路径收敛到 `cmake/Dependencies.cmake`。
2. 视项目规模决定是否把 `src/engine/` 改为更明确的 `src/rknn/`。
3. 如果新增 YOLOv8、车牌识别等模型，再把 `src/yolov5/` 扩展为 `src/models/yolov5/`。
4. 拆分 `src/media/mpi_enc.cpp` 中的编码、封装和推流职责，降低媒体层修改风险。
