"""Convert Rockchip's optimized YOLOv8n-Pose ONNX model to RK3588 RKNN.

The hybrid-quantization exclusions follow the Apache-2.0 RKNN Model Zoo
``examples/yolov8_pose/python/convert.py`` reference implementation.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from rknn.api import RKNN


HYBRID_LAYERS = [
    ["/model.22/cv4.0/cv4.0.0/act/Mul_output_0", "/model.22/Concat_6_output_0"],
    ["/model.22/cv4.1/cv4.1.0/act/Mul_output_0", "/model.22/Concat_6_output_0"],
    ["/model.22/cv4.2/cv4.2.0/act/Mul_output_0", "/model.22/Concat_6_output_0"],
]


def require_success(ret: object, stage: str) -> None:
    if ret not in (None, 0):
        raise RuntimeError(f"{stage} failed with code {ret}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("onnx_model", type=Path)
    parser.add_argument("dataset", type=Path, help="text file containing representative image paths")
    parser.add_argument("output", type=Path)
    parser.add_argument("--target", default="rk3588")
    parser.add_argument("--fp16", action="store_true", help="disable INT8 quantization for accuracy comparison")
    args = parser.parse_args()

    if not args.onnx_model.is_file():
        raise FileNotFoundError(args.onnx_model)
    if not args.fp16 and not args.dataset.is_file():
        raise FileNotFoundError(args.dataset)
    args.output.parent.mkdir(parents=True, exist_ok=True)

    rknn = RKNN(verbose=True)
    try:
        require_success(rknn.config(
            mean_values=[[0, 0, 0]],
            std_values=[[255, 255, 255]],
            target_platform=args.target,
        ), "config")
        require_success(rknn.load_onnx(model=str(args.onnx_model)), "load ONNX")
        if args.fp16:
            require_success(rknn.build(do_quantization=False), "FP16 build")
        else:
            require_success(rknn.hybrid_quantization_step1(
                dataset=str(args.dataset), proposal=False, custom_hybrid=HYBRID_LAYERS
            ), "hybrid quantization step 1")
            stem = args.onnx_model.stem
            require_success(rknn.hybrid_quantization_step2(
                model_input=f"{stem}.model",
                data_input=f"{stem}.data",
                model_quantization_cfg=f"{stem}.quantization.cfg",
            ), "hybrid quantization step 2")
        require_success(rknn.export_rknn(str(args.output)), "export RKNN")
    finally:
        rknn.release()
    print(f"RKNN model written to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
