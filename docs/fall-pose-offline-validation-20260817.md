# RK3588 骨架、7类动作与跌倒告警回放记录

日期：2026-08-17  
开发板：Orange Pi 5 Ultra / RK3588  
发布目录：`/opt/rk3588-camera/releases/20260817-fall-02/package`

## 这次修正了什么

1. 前端原来只读了人员框，虽然后端已输出17个关键点，但没有绘制代码。现在监控画面用 SVG 连接 COCO 骨架，普通姿态为蓝色，跌倒候选/确认为红色。
2. 恢复原仓库的7个动作名称：`standing`（站立）、`walking`（行走）、`sitting`（坐姿）、`lying_down`（躺卧）、`stand_up`（起身）、`sit_down`（落座）、`fall_down`（跌倒）。
3. 人体竖框转横框时，单靠 IoU 容易换 ID。跟踪器现在结合 IoU 和中心距离，跌倒过程中的运动历史不再被清空。
4. 离线视频改用 `帧号 / FPS` 作为时间轴。原来用程序处理速度计时，离线推理越快，反而越难满足0.7秒确认时长。
5. 根据真实视频曲线，将髋部归一化下落阈值从 `0.45` 调整到 `0.22`。样本实际峰值为 `0.328`，旧阈值永远不会触发。
6. 修复事件录像就绪消息把 `recovered` 重置成 `confirmed`、并覆盖结束时间的问题。

## 离线视频与实测结果

测试视频使用公开项目 `punpayut/Fall-Detection` 的 `fall_example_1.mp4`，640×360，23.98 FPS，272帧，11.343秒。原始链接：

`https://github.com/punpayut/Fall-Detection/raw/refs/heads/main/deployment/huggingface_space/fall_example_1.mp4`

| 项目 | 结果 |
| --- | --- |
| 完整解码 | 272/272帧（结尾另有1条录像就绪更新） |
| 人员跟踪 | 271个有人帧全部为 `track_id=1` |
| 动作计数 | 站立27、行走60、落座8、坐姿4、跌倒100、躺卯72 |
| 起身 | 此视频没有起身片段；上升/下降方向分支已通过单元测试 |
| 算法告警 | 真实产生 `confirmed`，不是测试接口注入 |
| WebSocket | 收到 `alarm`，置信度 `0.966` |
| 事件状态 | 最终 `recovered`，结束时间晚于发生时间3.461秒 |
| 录像 | 11.343秒，535668字节，`recording_status=ready` |
| 录像下载 | `GET /api/events/{id}/video` 返回 HTTP 200 / `video/mp4` |
| 实时管线恢复 | 离线测试后恢复 RTSP 检测，约19 FPS，`last_error=null` |

最终端到端告警 ID：`fa595b19201046a6890b959475bcf395`。  
板端录像：`/var/lib/rk3588-camera/events/20260817-174736-fa595b19201046a6890b959475bcf395.mp4`。  
板端 CLI 详细结果：`/var/lib/rk3588-camera/offline-seven-pose-cli-final/`。

## 过程中遇到的额外问题

- OpenCV 解码在板端自动选了 `h264_rkmpp`，首次只得到32帧且报 RGA/解码错误。切换 `--decoder ffmpeg-software` 后完整得到272帧。
- 创建新发布时 `cp -a current` 保留了软链接，导致新目录仍指向旧发布。改为解引用的完整独立副本后，再原子切换 `current`。
- 历史管线日志属于 root，`rkcamera` 服务只能忽略写入失败。修正 `/var/log/rk3588-camera` 所有权；安装脚本已包含相同的 chown 逻辑。
- 浏览器可能缓存旧 JS/CSS，因此静态资源版本参数已换成 `pose-seven-20260817-2`。

## 边界与下一步

现在7类是基于 YOLOv8 关键点几何和短时运动历史的板端轻量判断，不是原项目的 PyTorch ST-GCN 分类器原样移植。好处是不额外占用 PyTorch/CPU，局限是单段视频不能证明7类在各种机位下的准确率。

正式上线前仍需用实际摄像头录制起身、快速落座、弯腰、蹲下、躺床、跌倒和多人遮挡，逐类统计误报/漏报。若要达到原项目 ST-GCN 的学习式动作分类，需要另行准备已训练权重、转 ONNX/RKNN，并在现场数据上评估。
