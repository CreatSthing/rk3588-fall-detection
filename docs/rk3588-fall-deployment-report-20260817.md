# RK3588 跌倒检测实机部署记录（2026-08-17）

这份文档记录本次从“代码可测试”到“RK3588 板端可运行”的完整过程，方便后续复现、排障和继续做现场标定。文中不保存摄像头账号或密码。

## 1. 最终状态

- 开发板：Orange Pi 5 Ultra，RK3588 / 8 GiB，Ubuntu 20.04，内核 `5.10.160-rockchip-rk3588`。
- 板端地址：`192.168.207.201`（热点 DHCP 地址，换网络后可能变化）。
- 当前发布：`/opt/rk3588-camera/releases/20260817-fall-02/package`。
- 当前软链接：`/opt/rk3588-camera/current`。
- Web 控制台：`http://192.168.207.201:8000/`。
- 服务：`rk3588-web.service`，已启用开机启动，以非 root 用户 `rkcamera` 运行。
- 摄像头转发和跌倒管线随 Web 服务自动启动。
- 事件数据库：`/var/lib/rk3588-camera/events/events.db`。
- 事件录像：`/var/lib/rk3588-camera/events/*.mp4`。

## 2. 实际验证结果

| 检查项 | 结果 |
| --- | --- |
| YOLOv8n-Pose FP16 模型初始化 | 成功，RKNN Runtime 2.3.2 / 驱动 0.9.6 |
| NPU 单图推理 | 成功，街景图识别出人体并输出17个关键点 |
| 真实 RTSP 连续推理 | 30帧和60帧测试均完整结束，无 RGA 转换错误 |
| systemd 常驻运行 | 成功，自动启动转流和跌倒算法 |
| 实时性能 | FP16 + 同步预览约11.4 FPS，640×360输入 |
| Web 页面和静态资源 | 端口8000从局域网访问均返回 HTTP 200 |
| WebRTC/HLS 播放入口 | 端口8889和8888均返回 HTTP 200 |
| WebSocket 告警 | 测试跌倒事件能够立即收到 `alarm` 消息 |
| SQLite 持久化 | 服务重启后测试事件和确认状态仍存在 |
| 事件录像 | 自动生成20.3秒、476889字节 MP4，`ffprobe`验证通过 |
| 录像下载接口 | 返回 HTTP 200 和 `video/mp4` |
| 非 root NPU权限 | `rkcamera` 用户单图推理成功 |
| 真实离线跌倒回放 | 272/272帧，告警置信度0.966，WebSocket推送和11.343秒录像成功 |
| 前端姿态展示 | 输出17点骨架并显示原项7类动作 |
| 本地/板端自动测试 | 10项测试全部通过 |

旧 INT8 运行快照曾达到19.93 FPS，但后续发现其量化置信度失真，不能作为生产性能结论。当前 FP16 运行快照为11.41 FPS；systemd重启次数为0，`last_error`为空。该快照只证明短时运行正常，不能替代后续24～72小时长稳测试。

当前摄像头画面是空床位，没有人员，因此仍未做本机位真人模拟跌倒。后续已增加一段公开真实跌倒视频的完整回放，算法本身产生了告警、WebSocket推送和录像；详细数据见 [`fall-pose-offline-validation-20260817.md`](fall-pose-offline-validation-20260817.md)。正式上线前仍必须录制现场跌倒、起身、落座、弯腰、躺床等视频做误报/漏报标定。

## 3. 模型和运行库

模型没有提交到 Git，因为它是生成文件。板端文件：

```text
/opt/rk3588-camera/current/assets/weights/yolov8n-pose-fp16.rknn
```

- ONNX 来源：Rockchip RKNN Model Zoo 的优化版 `yolov8n-pose.onnx`。
- 转换工具：RKNN-Toolkit2 2.3.2。
- 精度：FP16，不使用量化校准集。
- RKNN SHA-256：`3d63f96d834d0d73c3e7e32bac7ca084d820548a9f394bf62ae1c9f526793c17`。
- 详细来源、版本和校验值：`deploy/fall-model-manifest.json`。

板端 `/usr/lib/librknnrt.so` 已升级到2.3.2。旧的1.4.0运行库保存在：

```text
/usr/lib/librknnrt.so.1.4.0-backup-20260817
```

## 4. 遇到的问题、原因和解决办法

### 4.1 Python依赖不存在

- 表现：系统 Python 和原 Web 虚拟环境无法导入 `cv2`、`numpy`、`rknnlite`。
- 原因：原项目主链路是 C++ YOLOv5，没有安装 Python 姿态推理依赖。
- 解决：安装板卡发行版的 `python3-opencv`、`python3-numpy`，新发布目录使用独立虚拟环境，并安装与 Python 3.8/aarch64 匹配的 RKNNLite 2.3.2 wheel。

### 4.2 模型版本不兼容

- 表现：`Invalid RKNN model version 6`。
- 原因：模型由 Toolkit2 2.3.2生成，但板端原 Runtime 是1.5.3；RKNNLite还固定加载 `/usr/lib/librknnrt.so`，该文件更旧，只有1.4.0。仅设置 `LD_LIBRARY_PATH` 无法覆盖硬编码路径。
- 解决：校验官方2.3.2运行库后备份旧文件，再安装到 `/usr/lib/librknnrt.so`。旧 YOLOv5 发布仍携带自己的1.5.3运行库，可回滚。

### 4.3 RKNNLite输入维度不同

- 表现：`The input[0] need 4dims input, but 3dims input buffer feed`。
- 原因：PC参考示例可传三维图像，新版板端 Lite API 要求显式 batch 维度。
- 解决：输入从 `[H,W,C]` 改为 `[1,H,W,C]`，并声明 `NHWC`。

### 4.4 关键点输出形状不同

- 表现：关键点索引越界。
- 原因：板端真实输出是 `[1,17,3,8400]`，原适配器按 `[1,51,8400]` 处理。
- 解决：增加输出形状归一化，统一变为 `[51,8400]`，并加入回归测试。

### 4.5 硬件解码/RGA步长错误

- 表现：640×360摄像头流持续出现 `RgaBlit fail: Invalid argument`，日志量很大。
- 原因：板卡定制 FFmpeg/OpenCV 自动选择 `h264_rkmpp`，解码帧的对齐步长与 RGA 转换路径不匹配。
- 解决：网络流由独立 FFmpeg 子进程读取，并显式选择软件 `h264` 解码，输出 BGR24 原始帧给 NPU。实测不再出现 RGA 错误，稳定约17～20 FPS。

### 4.6 OpenCV无法写事件MP4

- 表现：`avc1`、`H264`、`mp4v` 均无法打开，提示没有可用 `v4l2m2m` 编码设备。
- 原因：OpenCV自动选择了板端硬件编码后端，但当前设备节点/插件组合不可用。
- 解决：事件帧仍以 JPEG 放在内存环形缓冲区，异步写录像时改用 FFmpeg 并显式选择软件 `libx264`。2秒合成录像和20.3秒真实流录像均验证成功。

### 4.7 systemd首次启动返回203/EXEC

- 表现：服务启动后立即退出，状态是 `203/EXEC`。
- 原因：原仓库若干 `.sh` 文件在 Git 中没有可执行位，新克隆目录无法执行 `run_web.sh`。
- 解决：补齐板端权限，并把4个部署脚本的可执行位固化进 Git。

### 4.8 正常RKNN提示被误判为故障

- 表现：算法持续输出帧，但前端 `last_error` 显示动态范围查询失败。
- 原因：静态模型不支持动态范围查询是可忽略提示，但日志包含 `failed/error`，被通用错误规则捕获。
- 解决：仅对同时包含“static shape”的两种已知提示降级为 warning；重启后 `last_error` 为空。

### 4.9 板端直连GitHub很慢

- 表现：7.7 MiB运行库下载数分钟仍未完成。
- 原因：开发板当前网络访问 GitHub Raw 链路速度很低。
- 解决：在PC下载，核对 SHA-256 后通过 SCP 传到板端。正式发布不要依赖板端临时联网下载大文件。

### 4.10 骨架没有贴在人体上

- 表现：浏览器画面里能看到人体和骨架，但二者位置/时刻不一致。
- 原因：视频来自 WebRTC iframe，关键点来自独立 RTSP 推理，两条链路延迟不同，iframe 内部缩放也无法由外层 SVG 精确复现。
- 解决：推理管线把同一帧 JPEG 和检测 JSON 成对通过 WebSocket 发送；前端只用这一对数据同时更新图片、框和骨架。后端日志省略 Base64 正文，状态接口也不持久化图片，避免内存和日志膨胀。

### 4.11 INT8 把手或边缘误认为人

- 表现：所有人体置信度都是 `0.500`，包括明显误检。
- 原因：旧 INT8 仅用40张无人体空场景校准，目标置信度输出发生饱和；真正人体也只能输出 sigmoid(0)=0.5。
- 解决：生产模型切换为 FP16，并按 Rockchip 官方解码规则使用严格 `score > threshold`。同一跌倒帧置信度由固定0.500恢复到0.8106；离线端到端复测得到0.82告警和可下载事件录像。旧 INT8 只保留回滚，必须使用覆盖7类姿态和困难负样本的数据重新量化、独立验证后才能启用。

## 5. 日常运维命令

```bash
systemctl status rk3588-web --no-pager
journalctl -u rk3588-web -f
curl http://127.0.0.1:8000/api/status
curl http://127.0.0.1:8000/api/events?limit=20
```

重新执行真实流验收：

```bash
/opt/rk3588-camera/current/deploy/verify_fall_deployment.sh \
  /opt/rk3588-camera/current \
  rtsp://127.0.0.1:8554/live/cam1 30
```

模型、Runtime和wheel都必须先按 `deploy/fall-model-manifest.json` 核对 SHA-256。

## 6. 回滚信息

旧发布保留在：

```text
/opt/rk3588-camera/releases/20260813-vm-context20/package
```

原 Web 配置备份：

```text
/etc/rk3588-camera/web.json.before-fall-20260817
```

回滚时应同时恢复旧软链接、旧配置、旧 service 文件和旧 `/usr/lib/librknnrt.so`，然后执行 `systemctl daemon-reload` 和 `systemctl restart rk3588-web`。不要只切软链接，因为当前 Web 配置中的算法命令已经改成跌倒检测入口。

## 7. 后续现场验收

1. 在当前机位采集真实/模拟跌倒、快速坐下、弯腰、下蹲、躺床、遮挡、夜间和多人交叉视频。
2. 分别统计漏报和误报，再调整下降速度、水平姿态、确认时长和关键点置信度；不要只凭一两个样本改阈值。
3. 做至少24小时连续运行，生产前建议72小时；记录 FPS、温度、内存、磁盘、重启次数和录像成功率。
4. 验证断摄像头、断网、磁盘写满、服务崩溃和系统重启后的恢复行为。
5. 当前方案是视觉辅助告警，不能作为唯一的医疗或生命安全保障。
