#!/usr/bin/env python3
"""Create reproducible calibration and validation lists from a YOLO-format dataset."""

import argparse
import random
from pathlib import Path
from typing import List


IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}


def has_person(image_path: Path, label_dir: Path) -> bool:
    label_path = label_dir / f"{image_path.stem}.txt"
    if not label_path.exists():
        return False
    for line in label_path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if fields and fields[0] == "0":
            return True
    return False


def write_list(paths: List[Path], output: Path, relative_to: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8", newline="\n") as stream:
        for path in paths:
            stream.write(path.relative_to(relative_to).as_posix())
            stream.write("\n")


def parse_args() -> argparse.Namespace:
    project_root = Path(__file__).resolve().parents[1]
    default_root = project_root / "assets" / "calibration" / "coco128"
    parser = argparse.ArgumentParser(
        description="Split a YOLO-format dataset into calibration and validation image lists."
    )
    parser.add_argument(
        "--image-dir",
        type=Path,
        default=default_root / "images" / "train2017",
        help="Directory containing dataset images.",
    )
    parser.add_argument(
        "--label-dir",
        type=Path,
        default=default_root / "labels" / "train2017",
        help="Directory containing YOLO label files.",
    )
    parser.add_argument(
        "--calibration-output",
        type=Path,
        default=project_root / "assets" / "calibration" / "coco128_calibration.txt",
        help="Output list used by RKNN INT8 calibration.",
    )
    parser.add_argument(
        "--validation-output",
        type=Path,
        default=project_root / "assets" / "calibration" / "coco128_validation.txt",
        help="Output list reserved for metric validation.",
    )
    parser.add_argument(
        "--validation-ratio",
        type=float,
        default=0.22,
        help="Fraction reserved for validation, default 0.22 (128 -> 28).",
    )
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    image_dir = args.image_dir.resolve()
    label_dir = args.label_dir.resolve()
    calibration_output = args.calibration_output.resolve()
    validation_output = args.validation_output.resolve()
    if not image_dir.exists():
        raise FileNotFoundError(f"image directory not found: {image_dir}")
    if not label_dir.exists():
        raise FileNotFoundError(f"label directory not found: {label_dir}")
    if not 0 < args.validation_ratio < 1:
        raise ValueError("--validation-ratio must be between 0 and 1")
    if calibration_output == validation_output:
        raise ValueError("calibration and validation outputs must be different")

    images = sorted(
        path.resolve()
        for path in image_dir.rglob("*")
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    )
    if len(images) < 2:
        raise RuntimeError("at least two images are required")

    validation_count = max(1, min(len(images) - 1, round(len(images) * args.validation_ratio)))
    groups = {
        True: [path for path in images if has_person(path, label_dir)],
        False: [path for path in images if not has_person(path, label_dir)],
    }
    rng = random.Random(args.seed)
    for paths in groups.values():
        rng.shuffle(paths)

    # Allocate validation images proportionally across person/non-person groups.
    raw_counts = {
        key: validation_count * len(paths) / len(images)
        for key, paths in groups.items()
    }
    validation_by_group = {
        key: min(len(paths), int(raw_counts[key]))
        for key, paths in groups.items()
    }
    remainder = validation_count - sum(validation_by_group.values())
    candidates = sorted(
        groups,
        key=lambda key: raw_counts[key] - validation_by_group[key],
        reverse=True,
    )
    for key in candidates:
        if remainder == 0:
            break
        if validation_by_group[key] < len(groups[key]):
            validation_by_group[key] += 1
            remainder -= 1

    validation = []
    calibration = []
    for key, paths in groups.items():
        validation.extend(paths[: validation_by_group[key]])
        calibration.extend(paths[validation_by_group[key] :])

    rng.shuffle(validation)
    rng.shuffle(calibration)
    # RKNN resolves dataset entries relative to the dataset txt directory.
    write_list(calibration, calibration_output, calibration_output.parent)
    write_list(validation, validation_output, validation_output.parent)

    print(f"images: {len(images)}")
    print(f"calibration: {len(calibration)} -> {calibration_output}")
    print(f"validation: {len(validation)} -> {validation_output}")
    print(f"calibration person/non-person: {sum(has_person(p, label_dir) for p in calibration)}/{sum(not has_person(p, label_dir) for p in calibration)}")
    print(f"validation person/non-person: {sum(has_person(p, label_dir) for p in validation)}/{sum(not has_person(p, label_dir) for p in validation)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
