# RK3588 YOLOv5 Profiling 实战与简历素�?
日期�?026-08-12
平台：Orange Pi 5 Ultra，RK3588�? GiB，RKNPU 驱动 0.9.6，RKNN Runtime 1.5.3b6
负载：YOLOv5s INT8，输�?640×640；测试视�?H.264�?280×720�?5 FPS

## 1. Profiling 到底解决什么问�?
�?FPS 只能说明系统每秒交付多少帧，不能回答“时间花在哪里”“画面是否已经过时”“哪�?NPU 核在工作”以及“内存和温度是否持续增长”。本项目按四层采集数据：

```mermaid
flowchart LR
    A["视频输入"] --> B["解码 decode"]
    B --> C["有界队列 queue"]
    C --> D["预处�?preprocess"]
    D --> E["RKNN / NPU"]
    E --> F["后处�?postprocess"]
    F --> G["画框 draw"]
    G --> H["结果交付"]
    I["进程 CPU / 线程 / RSS"] -. pidstat .-> B
    I -. pidstat .-> D
    J["整机�?CPU �?] -. mpstat .-> A
    K["NPU 三核负载 / 温度"] -. debugfs + sysfs .-> E
    L["RKNN 算子级耗时"] -. RKNN query .-> E
```

- 帧级：decode、queue、preprocess、NPU、postprocess、draw、end-to-end�?- 算子级：RKNN 整图耗时和每层耗时，定位模型热点�?- 进程/系统级：线程 CPU、整�?CPU、RSS、SoC/NPU 温度、NPU 三核负载�?- 子系统隔离：单独�?FFmpeg 软件解码�?MPP 硬解，避免把集成故障误判为硬件性能不足�?
## 2. 已实现的采集方式

### 2.1 帧级埋点

使用 C++ `std::chrono::steady_clock`，因为它不受系统时间校准影响。计时点放在真实阶段边界�?
- `VideoCapture::read()` 前后：解�?取帧�?- 任务入队�?worker 取出：排队等待�?- `Preprocess()`、`rknn_run()`、`Postprocess()` 前后：前处理、NPU、后处理�?- 结果取出到画框结束：绘制�?- 入队到结果完成：端到端处理延迟�?
每帧�?CSV，程序退出时计算平均值、P50、P95、P99、最大值。工程上重点�?P95/P99：平均值会掩盖少量但明显的卡顿�?
CSV 字段为：

```text
frame_id,status,detections,decode_ms,queue_ms,preprocess_ms,npu_ms,postprocess_ms,draw_ms,e2e_ms
```

### 2.2 硬件时序采样

程序�?100 ms 读取�?
- `/proc/self/status` �?`VmRSS`：进程实际驻留内存�?- `/sys/class/thermal/thermal_zone0/temp`：SoC 温度�?- `/sys/class/thermal/thermal_zone6/temp`：NPU 温度�?- `/sys/kernel/debug/rknpu/load`：NPU Core0/Core1/Core2 负载�?
若帧 CSV �?`run.frames.csv`，硬件曲线自动写�?`run.frames.csv.hw.csv`，可直接�?Excel、Python/pandas �?Grafana 绘图�?
### 2.3 RKNN 算子�?Profiling

代码在创�?RKNN context 时按环境变量启用 `RKNN_FLAG_COLLECT_PERF_MASK`，随后查询：

- `RKNN_QUERY_PERF_RUN`：一次完整计算图运行时间�?- `RKNN_QUERY_PERF_DETAIL`：每�?NPU 算子的类型、形状和耗时�?
运行方式�?
```bash
RKNN_PERF=1 RKNN_PERF_DETAIL=1 \
  /opt/rk3588-camera/current/bin/yolov5_img \
  /opt/rk3588-camera/current/share/weights/yolov5s_raw_heads_int8.rknn \
  /opt/rk3588-camera/current/share/media/000057.jpg /tmp/result.jpg
```

详细采集有额外开销，只在实验环境开启，生产默认关闭�?
### 2.4 一键联合采�?
项目提供 `tools/profile_rk3588.sh`，同时启动应用内埋点、`pidstat` �?`mpstat`�?
```bash
chmod +x tools/profile_rk3588.sh
tools/profile_rk3588.sh \
  /opt/rk3588-camera/current/share/weights/yolov5s_raw_heads_int8.rknn \
  /tmp/bj_short.mp4 /tmp/profile/run 3 1 software
```

输出包括应用日志、逐帧 CSV、硬�?CSV、线�?进程 CPU 日志和各 CPU 核日志。`PROFILE_APP` 可覆盖待测程序路径�?
## 3. 实测结论与推理过�?
### 3.1 先定位吞吐，再定位延�?
同一�?481 帧视频，优化前可靠串行基线为 16.54 FPS；三 RKNN context 流水线为 78.17 FPS，提升约 4.7 倍。三�?context 让不同帧同时占用 RK3588 的三�?NPU 核，但并不会把单帧计算时间缩短为三分之一�?
初版 profiling 发现队列容量 10 时：

| 指标 | 队列 10 | 队列 3 | 变化 |
| --- | ---: | ---: | ---: |
| 吞吐 | 78.42 FPS | 77.20 FPS | -1.6% |
| 平均排队 | 119.89 ms | 35.82 ms | -70.1% |
| 平均端到�?| 167.88 ms | 85.61 ms | -49.0% |
| P95 端到�?| 193.97 ms | 108.39 ms | -44.1% |
| 峰�?RSS | 223.3 MiB | 205.2 MiB | -18.1 MiB |

因此把离线线程池待处理上限收紧到 3。核心经验是：实时视觉系统不能只追求 FPS；队列越深，吞吐可能不变，但用户看到的是更旧的画面�?
### 3.2 各阶段耗时

队列 3、三 context、开启画框的 2405 帧长测结果：

| 阶段 | 平均 | P95 | 判断 |
| --- | ---: | ---: | --- |
| 解码 | 5.23 ms | 9.19 ms | 不是主瓶�?|
| 排队 | 35.59 ms | 42.31 ms | �?context 流水线的主要延迟来源之一 |
| 预处�?| 5.26 ms | 9.10 ms | 仍可�?RGA/零拷贝继续优�?|
| NPU | 30.78 ms | 36.21 ms | 单帧最大计算阶�?|
| 后处�?| 0.65 ms | 0.98 ms | 占比很小 |
| 画框 | 4.08 ms | 6.93 ms | 对吞吐影响有�?|
| 端到�?| 83.21 ms | 99.75 ms | 2405/2405 帧成�?|

吞吐�?77 FPS，而单帧端到端�?83 ms，两者不矛盾：三帧可以在不同 context 中重叠处理�?
### 3.3 NPU 使用情况

- �?context：NPU 平均负载�?`61/0/0`，只使用 Core0�?- �?context 长测：平均约 `78/63/38`，峰�?`85/71/47`�?- 这验证了性能提升来自帧级并行利用三核，而不是单�?context 自动铺满三核�?
RKNN 详细报告中一次计算图运行�?32.679 ms，共解析 89 个算子。最热算子是首层高分辨率卷积 `Conv:/model.0/conv/Conv`，约 3.936 ms。它处理 320×320 特征图，后续若做模型级优化，应优先评估输入尺寸、stem 通道数、算子融合和更轻骨干网络，而不是先优化仅约 0.65 ms 的后处理�?
### 3.4 CPU、线程与解码对照

`pidstat -h -t -u -r -p PID 1` 显示�?context 进程约使�?132% CPU，即�?1.3 个逻辑核；`mpstat -P ALL 1` 显示 8 核总体�?14.42% user�?.35% system�?1.22% idle。说明单 context 时既没有用满 CPU，也没有用满三个 NPU 核，增加 context 是合理方向�?
隔离解码子系统后�?
- FFmpeg 软件 H.264 解码�?81 帧，墙钟 1.678 s，约 287 FPS；代价是累计 9.09 s user CPU，即使用多个 CPU 核�?- Rockchip `mpi_dec_test` 原生 MPP 解码�?81 帧，墙钟�?1.888 s，约 255 FPS�?
两者都远高于整条推理流水线需求。因此早�?`h264_rkmpp` 低速和丢帧不是 VPU 解码能力不足，而是 OpenCV/RKMPP 输出�?RGA 的格式、stride �?buffer 集成路径失败。生产版本应直接使用 MPP + DMA buffer，明确像素格式和 stride，并�?RGA/导入内存接口完成零拷贝或一次可控转换�?
### 3.5 MPP、DMA buffer、RGA 和零拷贝到底是什�?
- `MPP`：Rockchip Media Process Platform，是 RK3588 上调�?VPU 硬件编解码的用户态接口。简单说，H.264/H.265 视频压缩数据进来，MPP 让硬件解码器吐出 NV12/YUV 之类的原始图像帧�?- `DMA buffer` / `DMA-BUF`：Linux 里跨硬件模块共享内存的一�?fd 机制。MPP 解码出来的帧可以带一个文件描述符，例�?`dmabuf_fd=9`，RGA/NPU/显示模块可以拿这�?fd 访问同一块物�?buffer，不需�?CPU `memcpy`�?- `RGA`：Rockchip Raster Graphic Acceleration，是 2D 图像硬件加速器，常用于 resize、crop、颜色转换、旋转和画面合成。本项目里用它把 MPP 输出�?1080p NV12 转成模型需要的 RGB888/640×640�?- `零拷贝`：不是“不做任何处理”，而是“不把大图像帧来回拷�?CPU 内存”。理想链路是 MPP 解码到硬�?buffer，RGA 直接读这�?fd 做缩�?转色，再把结果写到另一个硬�?buffer，CPU 只传 fd 和参数�?
新增 `mpp_dma_rga_probe` 后，板端�?`c3_1080.mp4` 转出�?Annex-B H.264 裸流完成验证�?
```text
INFO_CHANGE width=1920 height=1080 h_stride=1920 v_stride=1088 buf_size=4177920
DECODED packets_used=2
FRAME width=1920 height=1080 h_stride=1920 v_stride=1088 fmt=YUV420SP/NV12 dmabuf_fd=9
RGA_DMABUF_OK src_fd=9 dst_fd=16 target=640x640 convert_ms=1.83596
```

这说明底层接口闭环已经通了：MPP 能输�?dmabuf fd，RGA 能直接用 fd �?NV12 �?RGB888 的转换。下一步生产化不是再证明硬件能不能跑，而是把这条探针路径接入真实视频流水线，并解决长期运行时的 buffer 生命周期、队列回压、错误恢复和 RKNN 输入 buffer 对接�?
后续新增 `mpp_rga_thread_pool` 后，原生链路已经接入项目推理入口：RGA 直接生成 640×640 RGB letterbox 图，YOLO 通过 `RunPreparedRgb` 跳过 OpenCV 预处理并复用原后处理逻辑。对 341 �?1080p H.264 样例，OpenCV 软件解码路径�?62.83 FPS；原�?MPP + DMA-BUF + RGA letterbox + prepared RGB 路径�?75.22 FPS，完整帧�?341/341，错误为 0�?
### 3.5 稳定性、温度和内存

2405 帧长测完成率 100%，推理错�?0�?
- 峰�?RSS�?10.9 MiB�?- 预热 5 秒后，早�?5 秒窗口平�?RSS 207.9 MiB，末�?5 秒为 209.5 MiB，未出现明显线性增长�?- 最�?SoC/NPU 温度�?6.4/55.5°C，开放环境下没有看到热降频迹象�?
这只是约 30 秒的工程冒烟/趋势测试，不应冒�?72 小时工业长稳结论。量产前仍需在最终机壳和环境温度下做 72 小时以上测试，并观察 RSS 趋势、频率、温度、NPU/IOMMU 错误、掉帧和自动恢复次数�?
## 4. 所用工具与各自用�?
| 工具 | 用�?| 关键输出 |
| --- | --- | --- |
| C++ `steady_clock` | 低开销阶段埋点 | 每帧 P50/P95/P99 |
| RKNN Query API | NPU 整图和算子级分析 | 热点层、单�?run 时间 |
| RKNPU debugfs | 三个 NPU 核利用率 | 是否真正并行 |
| thermal sysfs | SoC/NPU 温度 | 热稳定性、降频风�?|
| `/proc/self/status` | RSS 采样 | 内存峰值和趋势 |
| `pidstat` | 进程及线�?CPU/RSS | 哪些线程消�?CPU |
| `mpstat` | 每个 CPU 核利用率 | 整机是否 CPU 饱和 |
| FFmpeg `-benchmark` | 软件解码隔离基准 | wall/user/system/maxrss |
| `mpi_dec_test` | MPP/VPU 隔离基准 | 硬解能力与集成路径分�?|
| `mpp_dma_rga_probe` | MPP/RGA dmabuf 接口探针 | 验证硬解�?fd、stride、格式和 RGA fd 转换 |
| CSV + Excel/pandas | 趋势图、分位数和对�?| 可复核原始证�?|

板端没有与当前内核匹配的 `perf`，也没有 heaptrack/Valgrind，因此本轮没有伪�?CPU 火焰图或堆分配结论。后续若安装匹配内核符号�?`perf`，可�?`perf record -g` + FlameGraph 定位 CPU 前处理、颜色转换和内存复制热点；内存泄漏可在可接受性能开销的测试镜像中使用 ASan/LSan �?heaptrack 复核�?
## 5. 简历可直接使用的表�?
下面数字都有日志�?CSV 支撑，可按篇幅选用�?
- �?RK3588 上构�?YOLOv5 INT8 端到�?profiling 体系，使�?C++ 阶段埋点、RKNN 算子查询、RKNPU debugfs、pidstat/mpstat 与温�?RSS 时序采样，覆盖解码、队列、预处理、NPU、后处理和画框，并输出逐帧 P50/P95/P99 �?CSV�?- 将串行离线视频流水线重构为解码线�?+ 3 RKNN context + 有序结果回收的有界流水线，在 720p H.264�?81 帧完整无错误条件下由 16.54 FPS 提升�?78.17 FPS，约 4.7×�?- 基于逐帧 profiling 定位队列积压，将队列容量�?10 调整�?3，在吞吐仅下�?1.6% 的情况下，平均端到端延迟降低 49.0%，P95 降低 44.1%，峰�?RSS 减少 18.1 MiB�?- 通过 RKNN 89 算子详细报告定位首层高分辨率卷积为最大热点（3.936 ms），结合 NPU 三核负载证明�?context 将负载由单核 `61/0/0` 扩展到三核平�?`78/63/38`�?- 使用 FFmpeg �?Rockchip MPP 做解码隔离实验，测得�?287/255 FPS，证明低速丢帧源�?OpenCV-RKMPP-RGA 的格�?stride/buffer 集成路径，而非 VPU 算力不足�?- 绕开 OpenCV `h264_rkmpp` 黑盒插件，落�?FFmpeg demux + MPP 硬解 + DMA-BUF + RGA letterbox + prepared RGB 视频入口，在 1080p H.264 样例上完整处�?341/341 帧，吞吐由可靠软件路�?62.83 FPS 提升�?75.22 FPS�?- 完成 2405 帧压力冒烟测试，100% 完成�? 推理错误、约 77 FPS，P95 端到�?99.75 ms，开放环境最�?SoC/NPU 56.4/55.5°C，预热后 RSS 基本稳定�?
## 6. 面试时的讲述顺序

1. **背景**：�?FPS 提升后，仍不知道画面是否延迟、三核是否用满、慢在模型还是视频链路�?2. **方法**：建立从帧级、算子级到系统级的分�?profiling，并用分位数而不是只看平均值�?3. **发现**：NPU 是最大计算阶段，�?10 帧队列造成�?120 ms 平均等待；单 context 只占一�?NPU 核；原生解码器其实很快�?4. **行动**：三 context 帧级并行、队�?10�?、强制可靠软件解码作为当前基线，并把 MPP/RGA 零拷贝列为生产链路改造项�?5. **结果**：吞吐提�?4.7×；进一步在几乎不损失吞吐的情况下降低约一半端到端延迟；所有结论保留原始日志和 CSV�?6. **边界意识**�?405 帧测试只能证明短时稳定，工业验收还需要真实摄像头、最终机壳�?2 小时长稳、异常注入和环境/EMC 测试�?
## 7. 证据文件

原始数据位于 `output/board_validation/`�?
- `rk3588-profile.csv`：队�?10 的逐帧数据�?- `rk3588-profile-q3.csv`：队�?3 的逐帧数据�?- `rk3588-profile-long.csv` �?`.hw.csv`�?405 帧长测及硬件曲线�?- `rknn-perf-detail.log`：RKNN 89 算子详细报告�?- `pidstat-context1.log`、`mpstat-context1.log`：CPU/线程采样�?- 对应 `.log`：每次运行的汇总与环境证据�?