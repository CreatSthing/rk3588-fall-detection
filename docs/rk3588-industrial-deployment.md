# RK3588 工业化部署与验收记录

日期�?026-08-12
目标板：Orange Pi 5 Ultra（RK3588 / 8 GiB�?
部署地址：`root@192.168.204.201`（热�?DHCP 地址�?当前热点固定地址：`root@192.168.189.201`。NetworkManager 连接 `aaaaa` 保持 DHCP 自动获取网关/DNS，同时额外挂 `192.168.189.201/24`，避免写死网关导致热点换网段后断网�?
## 当前结论

项目已经在开发板上完成编译、安装和真实 NPU 推理。当前发布版本为�?
```text
/opt/rk3588-camera/releases/20260812-05/package
```

`/opt/rk3588-camera/current` 原子链接到该版本。旧版本仍保留，可通过切换软链接回滚�?
单图推理�?NPU 计算可作为当前生产基线；视频样例能够连续处理，但 Orange Pi 系统的视频硬�?RGA 转换路径持续报错并发生回退，因此视频实时链路尚未达到上线标准。HDMI IN 当前也没有有效输入信号，MIPI 摄像头没有枚举，不能完成真实摄像头端到端验收�?
## 已完成的工作

### 1. 对齐 NPU 软件�?
- 内核 RKNPU 驱动：`0.9.6`
- 系统�?Runtime：`1.4.0`，不能加�?RKNN v6 模型
- 项目私有 Runtime：`1.5.3b6`
- 模型：`yolov5s_raw_heads_int8.rknn`

项目私有 Runtime 能加载模型并在当前驱动上完成推理。没有覆�?`/lib` 下的系统 Runtime，避免影响板上其他程序，并保留了回滚路径�?
### 2. 构建和发布隔�?
- 安装 CMake、OpenCV 开发包和必要构建依赖�?- �?MPP/ZLMediaKit 流目标改为显式选项；缺少子模块头文件时在配置阶段明确失败�?- 完成全量基础目标构建：`yolov5_img`、`yolov5_video`、`yolov5_thread_pool`�?- 修复线程池目标漏链接 pthread 的问题�?- 使用 `$ORIGIN/../lib` RPATH，让发布包使用自身匹配的 RKNN Runtime�?- RGA 默认使用板端系统库，避免应用私有�?RGA 污染系统视频插件�?- 对关键二进制、动态库、模型和标签生成 `SHA256SUMS.critical`�?
### 3. 运行可靠�?
- 单图和视频入口现在会校验参数、输入、模型、输出文件和推理返回值�?- 错误情况返回稳定的非零退出码，可�?systemd 或监控判断�?- 视频连续出现 3 次推理错误时主动退出，由守护程序恢复，避免持续输出无效结果�?- 视频日志从每帧两组性能日志改为周期指标和最终汇总，避免日志风暴�?- RKNN 输出改用 `rknn_outputs_release()`，不再直�?`free()` Runtime 管理的内存�?- 模型加载完成后释放模型文件缓冲区，降低常驻内存�?
### 4. 服务和健康检�?
- 新建非登录用�?`rkcamera`，运行服务时不使�?root�?- 安装 `/etc/systemd/system/rk3588-camera.service`�?- 使用 `/etc/rk3588-camera/camera.env` 管理模型、输入、输出和温度阈值�?- 启动前检查发布文�?SHA-256、SoC 温度，并运行一次真�?NPU 单图推理�?- systemd 配置失败重启、启动频率限制、只读系统目录和基本权限隔离�?- 当前服务保持 `disabled`，因为视频输入没有有效信号，避免无意义的重启循环�?
## 实测数据

### 单图正确�?
测试图片尺寸 `637×457`，检测到 12 个目标。结果图中主要人物均被正确框出，坐标与人体基本贴合�?
```text
INFERENCE_OK detections=12 infer_ms=36�?3
```

独立�?Runtime 冒烟测试测得纯模型运行约 `23.8 ms`。入口统计还包含 OpenCV 预处理、后处理和对象转换，因此约为 `37 ms`�?
### NPU 算术正确�?
INT8 `64×64×64` 矩阵乘法�?`0.098 ms`�?096 个结果全部正确，错误数为 0�?
### 视频稳定�?
�?1080p�?5 FPS 样例连续运行 3 次：

- 共处理约 139 �?- 推理错误�?
- 新增 RKNPU/IOMMU 错误�?
- 平均推理、后处理和画框：�?`53�?5 ms/帧`
- 平均取帧/解码：约 `282�?88 ms/帧`
- 端到端吞吐：�?`2.8 FPS`
- 测试�?SoC 温度�?`35°C`，NPU 温度�?`34°C`
- 可用内存�?`6.9 GiB`，Swap 未使�?
当前瓶颈主要是视频解�?格式转换，不�?NPU。日志中�?RGA 失败来自系统 Rockchip 视频解码路径；程序能够回退并得到图像，但持续报错和低吞吐不满足生产要求�?
### 完整离线视频基准

使用 `bj_short.mp4`：H.264�?280×720�?5 FPS�?9.24 秒�?81 帧。当前系�?H.264/RGA 路径只向应用交付 65 帧，因此该路径测得的�?5.93 FPS 无效，不能作为正式性能数据�?
为测量不丢帧的完整流水线，先使用 FFmpeg 强制软件 H.264 解码并转换为相同分辨率、相同帧率、完�?481 帧的 MJPEG 离线视频，再运行同一 YOLO 程序�?
- 完整处理�?81/481 �?- 推理错误�?
- RGA 错误�?
- 总耗时�?9.089 �?- 端到端吞吐：�?16.54 FPS
- 平均取帧/解码�?0.665 ms/�?- 平均推理、后处理和画框：39.130 ms/�?- 测试�?SoC 温度�?33.3°C，NPU 温度�?32.4°C

这一结果代表“MJPEG 离线解码 + OpenCV 预处�?+ NPU 推理 + 后处�?+ 画框，但不写输出视频”的当前可靠基线。它不代表最�?MPP H.264 零拷贝流水线性能�?
### 2026-08-11 性能优化结果

针对上述串行瓶颈完成以下修改�?
1. 前处理从“先补成原图尺寸方形、再缩到 640×640”改为“直接等比缩到模型尺寸内、再补边”，并把 RGB 结果直接写入 RKNN 输入缓冲�?2. 检测与画框改为可分离，支持只输出结构化检测结果�?3. 视频读取、多�?RKNN context 和结果回收改为有界并行流水线�?4. 线程池使用精确的提交/完成计数，并传递每帧推理错误；只有完整处理且零错误才返回成功�?5. 增加 `decoder=auto|software`。当�?Orange Pi �?`h264_rkmpp` 路径会发�?RGA 错误和丢帧；离线 H.264 使用 `software` 强制选择 FFmpeg `h264` 解码器�?
同一份原�?H.264�?280×720�?81 帧视频，不写输出文件�?
| 模式 | 完成�?| 错误 | FPS |
| --- | ---: | ---: | ---: |
| 优化前串行、MJPEG 完整基准 | 481 | 0 | 16.54 |
| 优化后流水线�? context、H.264 软件解码 | 481 | 0 | 32.26 |
| 优化后流水线�? contexts、H.264 软件解码 | 481 | 0 | 57.02 |
| 优化后流水线�? contexts、H.264 软件解码 | 481 | 0 | 78.17 |
| 3 contexts、软件解码、开启画�?| 481 | 0 | 77.79 |

最终相对原可靠基线提升�?`4.7 倍`。满负载测试�?SoC/NPU �?50°C，内核新�?RKNPU/IOMMU 错误�?0�?
当前结果是离线吞吐能力，不等于实时延迟�? context 会同时处理不同帧，提高每秒处理量，但单帧仍需要排队、预处理和推理。真�?25 FPS 摄像头场景应使用容量受限队列，并在过载时丢弃旧帧，以保证结果不过时�?
优化后离线运行命令：

```bash
/opt/rk3588-camera/current/bin/yolov5_thread_pool \
  /opt/rk3588-camera/current/share/weights/yolov5s_raw_heads_int8.rknn \
  /path/to/input.mp4 \
  3 1 software
```

参数依次为：模型、视频、RKNN context 数量、是否画框、解码器模式�?
### 2026-08-12 分层 Profiling 与延迟优�?
新增逐帧 decode、queue、preprocess、NPU、postprocess、draw、end-to-end 埋点，输出平均值、P50/P95/P99 �?CSV；同时每 100 ms 采集 RSS、SoC/NPU 温度和三�?NPU 核负载。RKNN 可按环境变量输出整图�?89 个算子的详细耗时�?
Profiling 发现原待处理队列容量 10 造成平均 119.89 ms 排队。容量调整为 3 后，吞吐�?78.42 降至 77.20 FPS�?1.6%），平均端到端延迟从 167.88 降至 85.61 ms�?49.0%），P95 �?193.97 降至 108.39 ms�?44.1%），峰�?RSS �?223.3 降至 205.2 MiB�?
2405 帧压力冒烟测试全部完成且错误�?0，约 77 FPS，P95 端到�?99.75 ms，最�?SoC/NPU 56.4/55.5°C。单独测�?FFmpeg 软件解码和原�?MPP 解码分别约为 287/255 FPS，说明当前硬解问题位�?OpenCV/RKMPP �?RGA 的格式、stride �?buffer 集成路径，并�?VPU 性能不足�?
完整的方法、命令、数据解释和简历表述见 [`profiling-guide.md`](profiling-guide.md)�?
### 2026-08-12 MPP + DMA Buffer + RGA 零拷贝探�?
新增 `mpp_dma_rga_probe`，用于单独验证生产视频链路最关键的一跳：`MPP 解码�?-> DMA buffer fd -> RGA fd 输入 -> RGB888 输出 buffer`。它不经�?OpenCV `VideoCapture`，也不把 YUV 帧拷贝成 `cv::Mat` 后再处理�?
板端实测流程�?
```bash
ffmpeg -hide_banner -loglevel error -y \
  -i /opt/rk3588-camera/releases/20260812-05/assets/media/c3_1080.mp4 \
  -an -c:v copy -bsf:v h264_mp4toannexb /tmp/c3_1080.annexb.h264

/tmp/mpp_dma_rga_probe /tmp/c3_1080.annexb.h264 h264 640 640
```

实测输出核心信息�?
```text
INFO_CHANGE width=1920 height=1080 h_stride=1920 v_stride=1088 buf_size=4177920
DECODED packets_used=2
FRAME width=1920 height=1080 h_stride=1920 v_stride=1088 fmt=YUV420SP/NV12 dmabuf_fd=9
RGA_DMABUF_OK src_fd=9 dst_fd=16 target=640x640 convert_ms=1.83596
```

结论：MPP 可以输出�?DMA-BUF fd �?NV12 帧，RGA 可以直接�?fd 作为输入并转换到 RGB888 fd buffer；单�?1080p NV12 �?640×640 RGB888 转换�?`1.8 ms`。这证明底层“零拷贝接口闭环”成立，之前 OpenCV/RKMPP �?RGA 报错不是 VPU/RGA 硬件能力不足，而是上层插件�?buffer/stride/格式集成问题�?
当前探针是诊断工具，不是生产流水线：成功验证一帧后让进程退出，�?OS 回收 MPP/RGA 资源，以避开当前系统 MPP 在诊断程序退出释放路径上的崩溃坑。生产接入时需要把 frame/buffer 生命周期做成长期稳定的池化管理，并把 RGA 输出接到 RKNN 输入 buffer 或一个可控的 staging buffer�?
### 2026-08-12 原生 MPP/RGA 视频入口验证

新增 `mpp_rga_thread_pool`，绕开 OpenCV `h264_rkmpp` 插件，改为：FFmpeg 只负责拆包，MPP 负责硬解，RGA 使用 DMA-BUF fd �?NV12 �?640×640 RGB letterbox 转换，再作为 prepared RGB 输入送入 3 context YOLO 线程池，跳过 OpenCV 预处理路径�?
同一�?`c3_1080.mp4` 转出�?Annex-B H.264 裸流，完整处�?341 帧：

| 路径 | 完成�?| 错误 | FPS | 解码/转换指标 |
| --- | ---: | ---: | ---: | --- |
| OpenCV 软件 H.264 + 线程�?| 341/341 | 0 | 62.83 | decode avg 9.98 ms |
| 原生 MPP + DMA-BUF + RGA letterbox + prepared RGB | 341/341 | 0 | 75.22 | MPP/RGA avg 2.81 ms |

这版验证说明：替�?OpenCV/RKMPP 插件路径后，帧完整性恢复，RGA 报错消失，吞吐比可靠软件路径提升�?`19.7%`。当前已经保�?YOLO letterbox 几何信息，后处理框可按原图尺寸还原。它仍需继续做长稳和真实摄像头接入验证；另外当前 prepared RGB 仍通过普通内存传�?RKNN，后续可进一步改�?RKNN I/O memory 或固定输�?buffer 池�?
## 常用命令

手动健康检查：

```bash
sudo -u rkcamera /opt/rk3588-camera/current/bin/healthcheck
```

单图推理�?
```bash
/opt/rk3588-camera/current/bin/yolov5_img \
  /opt/rk3588-camera/current/share/weights/yolov5s_raw_heads_int8.rknn \
  /opt/rk3588-camera/current/share/media/000057.jpg \
  /tmp/result.jpg
```

视频测试�?
```bash
/opt/rk3588-camera/current/bin/yolov5_video \
  /opt/rk3588-camera/current/share/weights/yolov5s_raw_heads_int8.rknn \
  /opt/rk3588-camera/current/share/media/c3_1080.mp4 \
  - 100
```

摄像头接通且验收通过后启用服务：

```bash
sudo systemctl enable --now rk3588-camera
journalctl -u rk3588-camera -f
```

回滚示例�?
```bash
sudo ln -sfn /opt/rk3588-camera/releases/20260811-02/package /opt/rk3588-camera/current.next
sudo mv -Tf /opt/rk3588-camera/current.next /opt/rk3588-camera/current
sudo systemctl restart rk3588-camera
```

## 上线前阻断项

以下项目没有完成前，不应宣称整机达到工业量产标准�?
1. **摄像头链�?*：接入最终型号的 MIPI �?HDMI 摄像头，完成设备树、ISP/3A、曝光、逆光、低照度和断线重连测试�?2. **硬件视频链路**：补齐并锁定 MPP/ZLMediaKit 子模块版本，使用原生 MPP 解码�?DMA buffer 零拷贝，消除当前 RGA 报错；验证至少达到目�?FPS�?3. **长稳测试**：真实视频连续运行至�?72 小时，记�?FPS、延迟、温度、RSS、丢帧、NPU 错误和重启次数，并做结果趋势图�?4. **异常恢复**：逐项验证拔摄像头、断网、推流端关闭、模型损坏、磁盘写满、进程崩溃、NPU 超时和系统重启�?5. **掉电安全**：反复随机断电，确认文件系统、配置和模型不会损坏；生产设备建议只读根文件系统�?A/B 分区�?6. **热设�?*：在机壳、最高环境温度和满载条件下验证降频点；当前开放环�?35°C 不能代表密闭机壳�?7. **数据质量**：用目标现场数据评估漏检、误检、昼夜差异和分场景指标，设置可追溯的模型版本与阈值�?8. **安全**：禁�?root 密码远程登录、限�?SSH 来源、轮换设备密钥、保护流地址凭据、及时修补系统漏洞�?9. **可观测�?*：接入外部监控，至少上报存活、FPS、延迟、队列深度、温度、内存、磁盘、模型版本和最近错误�?10. **硬件合规**：按产品市场完成 EMC、ESD、浪涌、电源波动、振动、温湿度等测试；软件测试不能替代实验室认证�?
## 推荐验收门槛

门槛应根据业务修改，下面是一套可执行的初始值：

- 真实输入持续 72 小时，无进程崩溃、NPU/IOMMU 错误和不可恢复卡死�?- 稳态温度低于产品设定上限，且没有持续热降频�?- 端到�?FPS 达到输入帧率或明确的抽帧目标；P95 延迟满足业务要求�?- 队列有上限，过载时丢旧帧而不是无限增长内存�?- 拔插摄像头、断网和推流端故障后能在规定时间内自动恢复�?- 模型、二进制和配置都有版本号、校验值、发布记录和一键回滚路径�?- 正常运行使用�?root 用户，服务权限最小化�?