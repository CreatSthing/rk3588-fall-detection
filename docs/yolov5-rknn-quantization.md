# YOLOv5 RKNN 量化与验证

本项目在 Ubuntu 20.04 虚拟机中进行 RKNN 模型转换和量化。

```text
项目目录：/mnt/hgfs/yolov5_stream
Python 环境：/opt/rknn-venv
```

当前阶段统一使用 COCO128 数据集，不使用项目视频抽帧。

## 数据集角色

COCO128 同时包含图片和 YOLO 格式标签：

```text
图片：assets/calibration/coco128/images/train2017/
标签：assets/calibration/coco128/labels/train2017/
```

YOLO 的 COCO 类别编号中，`0` 表示 `person`。

数据必须拆成两部分：

```text
校准集：用于 INT8 量化，只需要图片。
验证集：不参与量化，使用图片和标签计算 Precision、Recall、mAP。
```

校准集和验证集不能重叠，否则量化后的精度结果会偏乐观。

## 生成 COCO128 校准清单

当前 COCO128 有 128 张图片。使用 `tools/split_coco_dataset.py` 以固定随机种子进行分层拆分：100 张校准、28 张验证；同时保证两边都包含有人的图片和无人的图片。

```bash
cd /mnt/hgfs/yolov5_stream
/opt/rknn-venv/bin/python tools/split_coco_dataset.py
```

生成的文件：

```text
assets/calibration/coco128_calibration.txt
assets/calibration/coco128_validation.txt
```

验证图片与校准图片没有交集；验证时读取同名 `.txt` 标签。

没有同名标签文件的验证图片按“无目标背景图”处理，不是数据损坏；它们用于统计误检。

## 导出浮点与 INT8 RKNN

先导出浮点模型，作为精度与速度基线：

```bash
cd /mnt/hgfs/yolov5_stream
/opt/rknn-venv/bin/python tools/convert_yolov5_onnx_to_rknn.py \
  --onnx assets/models/yolov5s.onnx \
  --output assets/weights/yolov5s_fp.rknn \
  --target rk3588 \
  --input-size 640 \
  --no-quant
```

再基于 COCO128 校准集生成 INT8 模型：

```bash
/opt/rknn-venv/bin/python tools/convert_yolov5_onnx_to_rknn.py \
  --onnx assets/models/yolov5s.onnx \
  --dataset assets/calibration/coco128_calibration.txt \
  --output assets/weights/yolov5s_int8.rknn \
  --target rk3588 \
  --input-size 640
```

其中 `rknn.build(do_quantization=True, dataset=...)` 才是真正执行 INT8 量化的步骤。

## 使用完整演示脚本

`tools/yolov5_convert.py` 会转换模型、运行一张图片、执行 Python 后处理，并保存检测结果。

生成 INT8 模型并运行演示：

```bash
cd /mnt/hgfs/yolov5_stream
/opt/rknn-venv/bin/python tools/yolov5_convert.py \
  --quantize \
  --dataset assets/calibration/coco128_calibration.txt \
  --output assets/weights/yolov5s_int8_demo.rknn
```

生成浮点模型：

```bash
/opt/rknn-venv/bin/python tools/yolov5_convert.py \
  --no-quant \
  --output assets/weights/yolov5s_fp_demo.rknn
```

## 验证量化效果

建议按以下顺序验证：

1. 结构一致性：确认 FP 和 INT8 都可加载，模型输入都是 `1 x 3 x 640 x 640`，输出数量和形状一致。
2. 单图检查：对同一张验证图片分别运行 FP 和 INT8，观察是否出现框位置明显偏移、行人漏检或大量误检。
3. 批量指标：在独立 COCO128 验证图片上，以 `person` 类别为重点，计算 Precision、Recall、AP50 和 mAP。
4. 性能：在 RK3588 使用相同输入测试 FP 和 INT8 的单帧耗时、FPS、内存和 NPU 使用情况。

判断 INT8 是否可用，不能只看模型文件变小或推理变快。重点是相比 FP 模型，`person` 的 Recall、Precision 和 AP 是否处在可接受下降范围内。

如果 INT8 效果明显变差，先运行诊断脚本定位问题：

```bash
cd /mnt/hgfs/yolov5_stream
/opt/rknn-venv/bin/python tools/diagnose_quantization.py \
  --onnx assets/models/yolov5s.onnx \
  --calibration-dataset assets/calibration/coco128_calibration.txt \
  --validation-list assets/calibration/coco128_validation.txt
```

诊断报告会写入：

```text
output/pc_validation/quantization_diagnosis.json
```

重点看四块信息：

1. `dataset_audit`：校准集和验证集是否重叠、路径是否丢失、person 数量和目标尺寸是否太少。
2. `metrics.delta_int8_minus_fp`：INT8 相对 FP 的 Precision、Recall、AP50 下降幅度。
3. `raw_output_diff_summary`：同一张图上 FP 与 INT8 原始输出的 MAE、RMSE、Cosine，相差越大越像量化本身损伤。
4. `worst_cases`：列出 INT8 漏掉 FP 命中的图、置信度明显下降的图和新增误检图，用来人工看图定位。

## 注意

- 当前 `yolov5s.onnx` 是 COCO 80 类模型，COCO128 标签可以直接用于验证。
- 量化使用 100 张 COCO128 图片仅适合跑通流程和做初步比较；正式部署前应使用更大的、与真实部署场景一致的带标注数据集复核。
- `tools/yolov5_convert.py` 与 `tools/validate_rknn_models.py` 支持三检测头和 ONNX 已合并的 `[1, 25200, 85]` 两种标准 YOLOv5 输出。替换为其他模型前，仍应先检查 ONNX 输出结构和类别数。
- RKNN C++ 推理会按输出数量自动选择后处理：当前单输出 `[1, N, 85]` 只执行置信度过滤、NMS 和坐标还原；后续裁剪 Detect 解码节点并导出三个原始检测头时，继续使用保留的三头 anchor 解码。
