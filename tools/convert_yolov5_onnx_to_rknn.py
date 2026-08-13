#!/usr/bin/env python3
import argparse
from pathlib import Path

from rknn.api import RKNN


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert and optionally quantize a YOLOv5 ONNX model to RKNN.")
    parser.add_argument("--onnx", required=True, help="Input YOLOv5 ONNX model.")
    parser.add_argument("--output", required=True, help="Output RKNN model path.")
    parser.add_argument("--dataset", help="Calibration dataset txt. Required for INT8 quantization.")
    parser.add_argument("--target", default="rk3588", help="RKNN target platform, default: rk3588.")
    parser.add_argument("--input-size", type=int, default=640, help="Model input size, default: 640.")
    parser.add_argument("--no-quant", action="store_true", help="Export FP model without INT8 quantization.")
    args = parser.parse_args()

    onnx_path = Path(args.onnx)
    output_path = Path(args.output)
    dataset_path = Path(args.dataset) if args.dataset else None

    if not onnx_path.exists():
        raise FileNotFoundError(f"ONNX model not found: {onnx_path}")
    if not args.no_quant and (dataset_path is None or not dataset_path.exists()):
        raise FileNotFoundError("INT8 quantization requires an existing --dataset txt")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    rknn = RKNN(verbose=True)
    try:
        ret = rknn.config(
            mean_values=[[0, 0, 0]],
            std_values=[[255, 255, 255]],
            target_platform=args.target,
        )
        if ret != 0:
            raise RuntimeError(f"rknn.config failed: {ret}")

        ret = rknn.load_onnx(model=str(onnx_path), inputs=["images"], input_size_list=[[1, 3, args.input_size, args.input_size]])
        if ret != 0:
            raise RuntimeError(f"rknn.load_onnx failed: {ret}")

        ret = rknn.build(
            do_quantization=not args.no_quant,
            dataset=str(dataset_path) if dataset_path else None,
        )
        if ret != 0:
            raise RuntimeError(f"rknn.build failed: {ret}")

        ret = rknn.export_rknn(str(output_path))
        if ret != 0:
            raise RuntimeError(f"rknn.export_rknn failed: {ret}")
    finally:
        rknn.release()

    print(f"exported: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
