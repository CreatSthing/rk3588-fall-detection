#!/usr/bin/env python3
"""Export YOLOv5 P3/P4/P5 raw detection heads without modifying YOLOv5 source."""

import argparse
import sys
from pathlib import Path
from types import MethodType

import torch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_YOLOV5_ROOT = PROJECT_ROOT.parent / "yolov5"
DEFAULT_WEIGHTS = PROJECT_ROOT / "assets" / "models" / "yolov5s.pt"
DEFAULT_OUTPUT = PROJECT_ROOT / "assets" / "models" / "yolov5s_raw_heads.onnx"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export three raw YOLOv5 heads for RKNN INT8 conversion.")
    parser.add_argument("--weights", type=Path, default=DEFAULT_WEIGHTS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--yolov5-root", type=Path, default=DEFAULT_YOLOV5_ROOT)
    parser.add_argument("--input-size", type=int, default=640)
    parser.add_argument("--opset", type=int, default=12)
    return parser.parse_args()


def raw_detect_forward(detect, features):
    """Return Detect convolution outputs before reshape, Sigmoid and anchor decoding."""
    return tuple(detect.m[index](features[index]) for index in range(detect.nl))


def main() -> int:
    args = parse_args()
    weights = args.weights.resolve()
    output = args.output.resolve()
    yolov5_root = args.yolov5_root.resolve()

    if not weights.is_file():
        raise FileNotFoundError(f"weights not found: {weights}")
    if not (yolov5_root / "models" / "yolo.py").is_file():
        raise FileNotFoundError(f"YOLOv5 source not found: {yolov5_root}")
    if args.input_size <= 0 or args.input_size % 32 != 0:
        raise ValueError("--input-size must be a positive multiple of 32")

    sys.path.insert(0, str(yolov5_root))
    from models.experimental import attempt_load
    from models.yolo import Detect

    model = attempt_load(str(weights), device=torch.device("cpu"), inplace=False, fuse=True)
    model.eval()
    detect = model.model[-1]
    if not isinstance(detect, Detect) or detect.nl != 3:
        raise TypeError("expected a YOLOv5 detection model with three Detect heads")

    # Only this loaded model instance is changed. The YOLOv5 source remains untouched.
    detect.forward = MethodType(raw_detect_forward, detect)
    sample = torch.zeros(1, 3, args.input_size, args.input_size)
    with torch.no_grad():
        heads = model(sample)

    expected_shapes = [
        (1, detect.na * detect.no, args.input_size // stride, args.input_size // stride)
        for stride in (8, 16, 32)
    ]
    actual_shapes = [tuple(head.shape) for head in heads]
    if actual_shapes != expected_shapes:
        raise RuntimeError(f"unexpected raw head shapes: {actual_shapes}, expected: {expected_shapes}")

    output.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        model,
        sample,
        str(output),
        opset_version=args.opset,
        do_constant_folding=True,
        input_names=["images"],
        output_names=["head_p3", "head_p4", "head_p5"],
    )

    import onnx

    exported = onnx.load(str(output))
    onnx.checker.check_model(exported)
    exported_shapes = [
        [dimension.dim_value for dimension in item.type.tensor_type.shape.dim]
        for item in exported.graph.output
    ]
    if exported_shapes != [list(shape) for shape in expected_shapes]:
        raise RuntimeError(f"exported ONNX shapes do not match: {exported_shapes}")

    print(f"exported: {output}")
    for name, shape in zip(("head_p3", "head_p4", "head_p5"), exported_shapes):
        print(f"{name}: {shape}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
